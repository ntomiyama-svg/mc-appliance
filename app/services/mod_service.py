"""List, upload and enable/disable mods & plugins for a registered server (v0.3).

A "mod" lives in ``<server_path>/mods`` and a "plugin" in
``<server_path>/plugins``. The two are handled identically here — the only
difference is the subdirectory name (``kind``).

Enable/disable follows the widely-used convention of appending ``.disabled`` to
the filename: an enabled mod is ``foo.jar`` and a disabled one is
``foo.jar.disabled``. Toggling just renames between the two.

Security invariants:

* All paths are resolved and checked to stay inside the registered server path
  (no traversal via a crafted ``kind`` or filename).
* Uploads must be ``.jar`` and keep only their basename (any directory part is
  stripped before use).
* There is **no delete** operation — disabling is the strongest action.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Dict, List

from fastapi import UploadFile

from app.config import ALLOWED_UPLOAD_EXTENSIONS, MAX_JAR_UPLOAD_BYTES
from app.models import Server

# The two managed kinds and their on-disk subdirectory.
VALID_KINDS = ("mods", "plugins")

_DISABLED_SUFFIX = ".disabled"


class ModError(Exception):
    """A user-facing problem managing a mod/plugin."""


def _validate_kind(kind: str) -> str:
    if kind not in VALID_KINDS:
        raise ModError("Unknown collection (must be 'mods' or 'plugins').")
    return kind


def collection_dir(server: Server, kind: str, create: bool = False) -> Path:
    """Resolve ``<server_path>/<kind>`` and confirm it stays inside the server."""
    _validate_kind(kind)
    base = Path(server.server_path).resolve()
    candidate = (base / kind).resolve()
    if candidate.parent != base:
        raise ModError("Resolved collection path escapes the server directory.")
    if create:
        candidate.mkdir(exist_ok=True)
    return candidate


def list_items(server: Server, kind: str) -> List[Dict[str, object]]:
    """Return ``[{name, enabled, size}]`` for the jars in a collection.

    ``name`` is the *enabled* base name (without the trailing ``.disabled``) so
    the UI can show a stable label regardless of state.
    """
    directory = collection_dir(server, kind)
    if not directory.is_dir():
        return []

    items: List[Dict[str, object]] = []
    for entry in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_file():
            continue
        fname = entry.name
        if fname.endswith(_DISABLED_SUFFIX):
            base_name = fname[: -len(_DISABLED_SUFFIX)]
            enabled = False
        elif fname.lower().endswith(ALLOWED_UPLOAD_EXTENSIONS):
            base_name = fname
            enabled = True
        else:
            # Ignore anything that is not a jar (or disabled jar).
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            size = 0
        items.append({"name": base_name, "enabled": enabled, "size": size})
    return items


async def save_upload(server: Server, kind: str, upload: UploadFile) -> str:
    """Stream a ``.jar`` upload into the collection, returning the stored name.

    The size cap is enforced while streaming; an oversized upload raises before
    the partial file is committed (it is written to a temp file first).
    """
    directory = collection_dir(server, kind, create=True)

    # Keep only the basename; reject anything that is not a .jar.
    raw_name = Path((upload.filename or "").strip()).name
    if not raw_name.lower().endswith(ALLOWED_UPLOAD_EXTENSIONS):
        raise ModError("Only .jar files may be uploaded.")

    dest = (directory / raw_name).resolve()
    if dest.parent != directory:
        raise ModError("Refusing to write outside the collection directory.")

    # Buffer to a temp file first so an oversized upload never lands as a
    # truncated jar in the collection.
    spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    total = 0
    try:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_JAR_UPLOAD_BYTES:
                raise ModError(
                    f"Uploaded jar exceeds the size limit "
                    f"({MAX_JAR_UPLOAD_BYTES // (1024 * 1024)} MiB)."
                )
            spool.write(chunk)
        if total == 0:
            raise ModError("Uploaded jar is empty.")
        spool.seek(0)
        with open(dest, "wb") as fh:
            shutil.copyfileobj(spool, fh)
    finally:
        spool.close()
    return raw_name


def toggle(server: Server, kind: str, name: str) -> str:
    """Flip a mod/plugin between enabled (``.jar``) and disabled (``.jar.disabled``).

    ``name`` is the enabled base name (e.g. ``foo.jar``). Returns a short status
    message. There is no delete: this only ever renames an existing file.
    """
    directory = collection_dir(server, kind)

    base_name = Path((name or "").strip()).name
    if not base_name.lower().endswith(ALLOWED_UPLOAD_EXTENSIONS):
        raise ModError("Invalid file name.")

    enabled_path = (directory / base_name).resolve()
    disabled_path = (directory / (base_name + _DISABLED_SUFFIX)).resolve()
    # Both targets must be direct children of the collection directory.
    if enabled_path.parent != directory or disabled_path.parent != directory:
        raise ModError("Refusing to operate outside the collection directory.")

    if enabled_path.is_file():
        enabled_path.rename(disabled_path)
        return f"Disabled {base_name}."
    if disabled_path.is_file():
        disabled_path.rename(enabled_path)
        return f"Enabled {base_name}."
    raise ModError(f"{base_name} was not found in {kind}/.")
