"""Fetch and cache the official Vanilla Minecraft server jar (v0.4).

This service talks to Mojang's public *version manifest* to discover available
Minecraft Java Edition versions, resolve the ``server.jar`` download URL for a
given version, download it, verify it, and cache it under ``JAR_CACHE_DIR``. A
cached jar can then be copied into a new server's directory by the Create Server
flow instead of requiring a browser upload.

Scope (v0.4)
------------
* **Vanilla only.** Paper / Fabric / Forge / NeoForge auto-fetch is *not*
  implemented (see docs/13_vanilla_jar_cache.md). ``server_type`` is always
  ``"Vanilla"`` and ``source`` is always ``"mojang"`` here.
* This is *separate* from starting servers and from RCON — caching a jar never
  launches anything.

Security model
--------------
* **No arbitrary-URL downloads.** The only URLs ever fetched are the manifest
  (``VANILLA_MANIFEST_URL``), the per-version metadata URL *listed in that
  manifest*, and the ``downloads.server.url`` *listed in that metadata*. The
  caller never supplies a URL — only a version id, which is validated and looked
  up in the manifest.
* Every URL we fetch is additionally checked against
  ``VANILLA_ALLOWED_DOWNLOAD_HOST_SUFFIXES`` (defence in depth) and must be
  ``https``.
* Downloads only ever land *inside* ``JAR_CACHE_DIR`` (path-traversal guarded by
  an allow-listed version id and a resolved-path check).
* If the manifest provides a SHA-1 it is verified; a mismatch deletes the file.
* Downloads are written to a temp file and atomically moved into place, so a
  failed/partial download never leaves a usable-looking jar behind.
* Re-downloading overwrites only the *same version* (the path is keyed by
  version), never an unrelated jar.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import (
    JAR_CACHE_DIR,
    VANILLA_ALLOWED_DOWNLOAD_HOST_SUFFIXES,
    VANILLA_AUTO_DOWNLOAD_ENABLED,
    VANILLA_HTTP_TIMEOUT_SECONDS,
    VANILLA_MANIFEST_URL,
)
from app.models import JAR_SOURCE_MOJANG, JAR_TYPE_VANILLA, ServerJarCache

logger = logging.getLogger("mc_appliance.vanilla")

# A Minecraft version id is conservative on purpose so it can be used directly
# as a directory name without any chance of path traversal. Real ids look like
# "1.20.4", "23w45a", "1.19-pre1", "1.8.9".
_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_USER_AGENT = "mc-appliance/0.4 (+https://example.invalid/mc-appliance)"
_MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024  # 512 MiB hard ceiling, sanity guard.


class VanillaDownloadError(Exception):
    """A user-facing problem while fetching/downloading a Vanilla jar."""


# --- URL safety ----------------------------------------------------------------


def _check_url(url: str) -> str:
    """Validate that ``url`` is https and on an allow-listed Mojang host."""
    if not isinstance(url, str) or not url:
        raise VanillaDownloadError("Missing download URL.")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise VanillaDownloadError("Refusing a non-HTTPS download URL.")
    host = (parsed.hostname or "").lower()
    if not any(
        host == suffix.lstrip(".") or host.endswith(suffix)
        for suffix in VANILLA_ALLOWED_DOWNLOAD_HOST_SUFFIXES
    ):
        raise VanillaDownloadError(f"URL host is not an allowed Mojang host: {host}")
    return url


def _http_get_json(url: str) -> dict:
    """GET ``url`` (allow-list checked) and parse the body as JSON."""
    _check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=VANILLA_HTTP_TIMEOUT_SECONDS) as resp:
            data = resp.read()
    except Exception as exc:  # urllib raises a zoo of errors; treat all as fetch fail.
        raise VanillaDownloadError(f"Could not reach Mojang: {exc}") from exc
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise VanillaDownloadError(f"Malformed JSON from Mojang: {exc}") from exc


# --- Manifest / metadata -------------------------------------------------------


def fetch_version_manifest() -> dict:
    """Fetch and return Mojang's ``version_manifest_v2.json`` as a dict."""
    manifest = _http_get_json(VANILLA_MANIFEST_URL)
    if not isinstance(manifest, dict) or "versions" not in manifest:
        raise VanillaDownloadError("Unexpected version manifest structure.")
    return manifest


def get_latest_release(manifest: Optional[dict] = None) -> str:
    """Return the latest *release* version id from the manifest."""
    manifest = manifest or fetch_version_manifest()
    latest = (manifest.get("latest") or {}).get("release")
    if not latest:
        raise VanillaDownloadError("Manifest does not declare a latest release.")
    return str(latest)


def _find_version_entry(manifest: dict, version: str) -> dict:
    """Return the ``versions[]`` entry for ``version`` or raise."""
    for entry in manifest.get("versions", []):
        if isinstance(entry, dict) and entry.get("id") == version:
            return entry
    raise VanillaDownloadError(f"Version '{version}' is not in the manifest.")


def fetch_version_metadata(version: str) -> dict:
    """Fetch the per-version metadata JSON for ``version``.

    Looks up the version's metadata URL in the manifest (never a caller-supplied
    URL) and fetches it.
    """
    version = _validate_version(version)
    manifest = fetch_version_manifest()
    entry = _find_version_entry(manifest, version)
    url = entry.get("url")
    if not url:
        raise VanillaDownloadError(f"Version '{version}' has no metadata URL.")
    return _http_get_json(url)


def get_server_download_info(version: str) -> Dict[str, object]:
    """Resolve the ``server.jar`` download info for ``version``.

    Returns ``{"url", "sha1", "size", "release_type", "manifest_url"}``. Raises
    if the version has no server download (some very old versions ship no
    dedicated server jar).
    """
    version = _validate_version(version)
    manifest = fetch_version_manifest()
    entry = _find_version_entry(manifest, version)
    metadata = _http_get_json(entry["url"])
    server = (metadata.get("downloads") or {}).get("server")
    if not server or not server.get("url"):
        raise VanillaDownloadError(
            f"Version '{version}' does not provide a server jar."
        )
    _check_url(server["url"])
    return {
        "url": server["url"],
        "sha1": server.get("sha1"),
        "size": server.get("size"),
        "release_type": entry.get("type"),
        "manifest_url": entry.get("url"),
    }


# --- Path helpers --------------------------------------------------------------


def _validate_version(version: str) -> str:
    version = (version or "").strip()
    if not _VERSION_RE.match(version):
        raise VanillaDownloadError("Invalid Minecraft version identifier.")
    return version


def _jar_path_for(version: str) -> Path:
    """Resolve the cache path for ``version`` strictly under JAR_CACHE_DIR."""
    base = Path(JAR_CACHE_DIR).resolve()
    candidate = (base / "vanilla" / version / "server.jar").resolve()
    # Defence in depth: the allow-listed version id cannot traverse, but verify.
    if base not in candidate.parents:
        raise VanillaDownloadError("Resolved jar path escapes the cache directory.")
    return candidate


# --- Download ------------------------------------------------------------------


def _download_to(url: str, dest: Path, expected_sha1: Optional[str]) -> int:
    """Download ``url`` to ``dest`` atomically, verifying SHA-1 if provided.

    Returns the number of bytes written. On any failure no file is left at
    ``dest`` (and any temp file is removed).
    """
    _check_url(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    sha = hashlib.sha1()
    total = 0
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), suffix=".part")
    tmp_path = Path(tmp_name)
    try:
        with urllib.request.urlopen(req, timeout=VANILLA_HTTP_TIMEOUT_SECONDS) as resp, \
                os.fdopen(tmp_fd, "wb") as out:
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_DOWNLOAD_BYTES:
                    raise VanillaDownloadError("Server jar exceeds the size ceiling.")
                sha.update(chunk)
                out.write(chunk)
        if total == 0:
            raise VanillaDownloadError("Downloaded server jar is empty.")
        if expected_sha1:
            actual = sha.hexdigest()
            if actual.lower() != str(expected_sha1).lower():
                raise VanillaDownloadError(
                    "SHA-1 mismatch — the downloaded jar was discarded."
                )
        os.replace(tmp_path, dest)  # atomic on the same filesystem.
        return total
    except Exception:
        # Never leave a partial/unsafe file behind.
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def download_server_jar(db: Session, version: str) -> ServerJarCache:
    """Download (or refresh) the Vanilla server jar for ``version`` and cache it.

    Upserts the matching :class:`ServerJarCache` row and refreshes the
    ``is_latest`` flags. Returns the cached row.
    """
    version = _validate_version(version)
    info = get_server_download_info(version)
    url = str(info["url"])
    dest = _jar_path_for(version)

    # Keep the download log line terse on purpose (one INFO line per download).
    logger.info("Downloading Vanilla %s server.jar from %s", version, url)
    size = _download_to(url, dest, info.get("sha1"))  # type: ignore[arg-type]

    row = (
        db.query(ServerJarCache)
        .filter(
            ServerJarCache.server_type == JAR_TYPE_VANILLA,
            ServerJarCache.version == version,
        )
        .first()
    )
    if row is None:
        row = ServerJarCache(server_type=JAR_TYPE_VANILLA, version=version)
        db.add(row)

    row.source = JAR_SOURCE_MOJANG
    row.release_type = info.get("release_type")
    row.jar_path = str(dest)
    row.manifest_url = info.get("manifest_url")
    row.download_url = url
    row.sha1 = info.get("sha1")
    row.size_bytes = size
    row.downloaded_at = datetime.utcnow()
    db.flush()

    # Mark this row latest iff it matches the manifest's latest.release. Best
    # effort — if the manifest is briefly unreachable we keep the prior flags.
    try:
        _mark_latest(db, get_latest_release())
    except VanillaDownloadError:
        pass

    db.commit()
    db.refresh(row)
    return row


# --- Cache state ---------------------------------------------------------------


def _mark_latest(db: Session, latest_version: str) -> None:
    """Set ``is_latest`` True only on the row matching ``latest_version``."""
    for row in db.query(ServerJarCache).filter(
        ServerJarCache.server_type == JAR_TYPE_VANILLA
    ):
        row.is_latest = row.version == latest_version
    db.flush()


def list_cached_vanilla_jars(db: Session) -> List[ServerJarCache]:
    """Return cached Vanilla jars, latest first then most-recently downloaded."""
    return (
        db.query(ServerJarCache)
        .filter(ServerJarCache.server_type == JAR_TYPE_VANILLA)
        .order_by(
            ServerJarCache.is_latest.desc(),
            ServerJarCache.downloaded_at.desc(),
            ServerJarCache.version.desc(),
        )
        .all()
    )


def jar_exists(row: ServerJarCache) -> bool:
    """True if the cached row points at a file that actually exists on disk."""
    return bool(row.jar_path) and Path(row.jar_path).is_file()


def get_cached_jar_path(db: Session, version: str) -> Optional[Path]:
    """Return the on-disk path of a cached Vanilla ``version`` jar, or None."""
    version = _validate_version(version)
    row = (
        db.query(ServerJarCache)
        .filter(
            ServerJarCache.server_type == JAR_TYPE_VANILLA,
            ServerJarCache.version == version,
        )
        .first()
    )
    if row and jar_exists(row):
        return Path(row.jar_path)
    return None


def get_latest_cached_jar(db: Session) -> Optional[ServerJarCache]:
    """Return the cached row flagged ``is_latest`` whose file exists, or None."""
    row = (
        db.query(ServerJarCache)
        .filter(
            ServerJarCache.server_type == JAR_TYPE_VANILLA,
            ServerJarCache.is_latest.is_(True),
        )
        .first()
    )
    return row if row and jar_exists(row) else None


def ensure_latest_vanilla_cached(
    db: Session, download: Optional[bool] = None
) -> Dict[str, object]:
    """Learn the latest release and refresh ``is_latest``; optionally download.

    Reaches out to Mojang for the latest release id, refreshes the cache's
    ``is_latest`` flags, and reports whether that version is cached. When
    ``download`` is True (or None and ``VANILLA_AUTO_DOWNLOAD_ENABLED``) and the
    latest is not yet cached, it downloads it.

    Returns ``{"latest_version", "cached", "downloaded", "error"}``. Network
    failures are reported via ``error`` rather than raised so callers (the
    dashboard) can degrade gracefully.
    """
    if download is None:
        download = VANILLA_AUTO_DOWNLOAD_ENABLED

    result: Dict[str, object] = {
        "latest_version": None,
        "cached": False,
        "downloaded": False,
        "error": None,
    }
    try:
        latest = get_latest_release()
    except VanillaDownloadError as exc:
        result["error"] = str(exc)
        return result

    result["latest_version"] = latest
    _mark_latest(db, latest)
    db.commit()

    cached_path = get_cached_jar_path(db, latest)
    if cached_path is not None:
        result["cached"] = True
        return result

    if download:
        try:
            download_server_jar(db, latest)
            result["cached"] = True
            result["downloaded"] = True
        except VanillaDownloadError as exc:
            result["error"] = str(exc)
    return result


def latest_cache_warning(db: Session) -> Optional[str]:
    """Return a dashboard warning string if the latest Vanilla jar is uncached.

    Never raises — any external-communication failure yields ``None`` so the
    dashboard (and the whole app) keeps working offline.
    """
    try:
        status = ensure_latest_vanilla_cached(db, download=False)
    except Exception:  # absolutely never break the dashboard.
        return None
    if status.get("error") or not status.get("latest_version"):
        return None
    if status.get("cached"):
        return None
    return (
        f"Latest Vanilla release {status['latest_version']} is not cached yet. "
        "Download it from JAR Cache to create servers without uploading a jar."
    )
