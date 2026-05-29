"""Vanilla server jar cache: list page + check/download actions (v0.4).

All routes here are admin-only via the global :class:`AuthMiddleware` (no extra
dependency needed). The GET renders the JAR Cache page; the POST actions return
JSON for the in-page buttons. Downloading only ever pulls the official Vanilla
server.jar resolved through Mojang's version manifest — there is no arbitrary
URL input (see :mod:`app.services.vanilla_downloader`).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.config import (
    JAR_CACHE_DIR,
    VANILLA_AUTO_CHECK_ENABLED,
    VANILLA_AUTO_DOWNLOAD_ENABLED,
    VANILLA_MANIFEST_URL,
)
from app.database import get_db
from app.services import vanilla_downloader
from app.templating import templates

router = APIRouter(prefix="/jars")


@router.get("/vanilla", response_class=HTMLResponse)
def vanilla_page(request: Request, db: Session = Depends(get_db)):
    """Render the Vanilla JAR Cache page (cached list + latest status)."""
    cached = vanilla_downloader.list_cached_vanilla_jars(db)
    # Best-effort: learn the latest release without downloading. Never fatal.
    latest_version = None
    check_error = None
    if VANILLA_AUTO_CHECK_ENABLED:
        status = vanilla_downloader.ensure_latest_vanilla_cached(db, download=False)
        latest_version = status.get("latest_version")
        check_error = status.get("error")
        # The flags may have changed; re-read so the table reflects them.
        cached = vanilla_downloader.list_cached_vanilla_jars(db)

    # Annotate each row with whether its file is actually present on disk.
    rows = [
        {"row": r, "available": vanilla_downloader.jar_exists(r)} for r in cached
    ]
    latest_cached = any(
        item["row"].is_latest and item["available"] for item in rows
    )
    return templates.TemplateResponse(
        request,
        "jars_vanilla.html",
        {
            "title": "JAR Cache",
            "rows": rows,
            "latest_version": latest_version,
            "latest_cached": latest_cached,
            "check_error": check_error,
            "auto_check": VANILLA_AUTO_CHECK_ENABLED,
            "auto_download": VANILLA_AUTO_DOWNLOAD_ENABLED,
            "cache_dir": str(JAR_CACHE_DIR),
            "manifest_url": VANILLA_MANIFEST_URL,
        },
    )


@router.post("/vanilla/check-latest")
def check_latest(db: Session = Depends(get_db)):
    """Ask Mojang for the latest release and whether it is cached (no download)."""
    status = vanilla_downloader.ensure_latest_vanilla_cached(db, download=False)
    if status.get("error"):
        return JSONResponse(
            {"ok": False, "message": f"Check failed: {status['error']}"}
        )
    latest = status.get("latest_version")
    cached = status.get("cached")
    msg = (
        f"Latest release is {latest} — "
        + ("already cached." if cached else "not cached yet.")
    )
    return JSONResponse(
        {"ok": True, "message": msg, "latest_version": latest, "cached": cached}
    )


def _download_response(db: Session, version: str) -> JSONResponse:
    try:
        row = vanilla_downloader.download_server_jar(db, version)
    except vanilla_downloader.VanillaDownloadError as exc:
        return JSONResponse({"ok": False, "message": f"Download failed: {exc}"})
    return JSONResponse(
        {
            "ok": True,
            "message": f"Cached Vanilla {row.version} ({row.size_bytes} bytes).",
            "version": row.version,
        }
    )


@router.post("/vanilla/download-latest")
def download_latest(db: Session = Depends(get_db)):
    """Download the latest Vanilla release server.jar into the cache."""
    try:
        latest = vanilla_downloader.get_latest_release()
    except vanilla_downloader.VanillaDownloadError as exc:
        return JSONResponse(
            {"ok": False, "message": f"Could not determine latest release: {exc}"}
        )
    return _download_response(db, latest)


@router.post("/vanilla/download/{version}")
def download_version(version: str, db: Session = Depends(get_db)):
    """Download a specific Vanilla version's server.jar into the cache."""
    return _download_response(db, version)
