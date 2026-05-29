"""Read and write server.properties for a registered server only.

The file is treated as a simple ``key=value`` properties file. On save we
write a ``.bak`` copy first and preserve any leading comment/blank lines so the
result is not gratuitously destructive, while accepting that exact inline
comment formatting may not be retained.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List, Tuple

from app.models import Server


def _safe_properties_path(server: Server) -> Path:
    """Resolve server.properties and verify it is inside the server directory."""
    base = Path(server.server_path).resolve()
    candidate = (base / "server.properties").resolve()
    if candidate.parent != base:
        raise ValueError("server.properties path escapes the server directory.")
    return candidate


def read_properties(server: Server) -> Tuple[Dict[str, str], bool]:
    """Return (ordered dict of key->value, exists flag).

    Order is preserved by relying on dict insertion order (Python 3.7+).
    """
    path = _safe_properties_path(server)
    props: Dict[str, str] = {}
    if not path.is_file():
        return props, False

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            props[key.strip()] = value
    return props, True


def write_properties(server: Server, new_values: Dict[str, str]) -> None:
    """Persist ``new_values`` to server.properties after backing up to .bak.

    Existing leading comment lines are preserved at the top of the file. Keys
    that already existed keep their original ordering; brand-new keys are
    appended at the end.
    """
    path = _safe_properties_path(server)

    existing_lines: List[str] = []
    if path.is_file():
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            existing_lines = fh.readlines()
        # Backup the current file before overwriting.
        shutil.copy2(path, path.with_suffix(".properties.bak"))

    # Preserve leading comments / blanks at the top of the original file.
    header: List[str] = []
    for raw in existing_lines:
        if raw.strip().startswith("#") or not raw.strip():
            header.append(raw.rstrip("\n"))
        else:
            break

    # Preserve original key ordering, then append any new keys.
    ordered_keys: List[str] = []
    seen = set()
    for raw in existing_lines:
        line = raw.rstrip("\n")
        if line.strip().startswith("#") or "=" not in line:
            continue
        key = line.partition("=")[0].strip()
        if key in new_values and key not in seen:
            ordered_keys.append(key)
            seen.add(key)
    for key in new_values:
        if key not in seen:
            ordered_keys.append(key)
            seen.add(key)

    out_lines: List[str] = list(header)
    for key in ordered_keys:
        out_lines.append(f"{key}={new_values[key]}")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out_lines) + "\n")
