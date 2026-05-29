"""System information page and the Java Runtime Manager."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.services import java_runtime, system_metrics
from app.templating import templates

router = APIRouter(prefix="/system")


@router.get("", response_class=HTMLResponse)
def system_page(request: Request):
    return templates.TemplateResponse(
        request,
        "system.html",
        {"title": "System", "info": system_metrics.get_system_info()},
    )


def _wants_json(request: Request) -> bool:
    """True if the caller asked for JSON (Accept header or ?format=json)."""
    if request.query_params.get("format") == "json":
        return True
    return "application/json" in (request.headers.get("accept") or "")


@router.get("/java")
def java_page(request: Request, db: Session = Depends(get_db)):
    """Java Runtime Manager.

    Serves the HTML management page by default, or the raw JSON Java inventory
    when the caller requests it (``Accept: application/json`` or
    ``?format=json``) — the front-end uses the JSON form to refresh in place.
    """
    info = java_runtime.get_java_info(db)
    if _wants_json(request):
        return JSONResponse(info)
    return templates.TemplateResponse(
        request,
        "java.html",
        {"title": "Java Runtimes", "info": info},
    )


@router.post("/java/rescan")
def java_rescan(db: Session = Depends(get_db)):
    """Re-detect installed Java runtimes and return the fresh inventory."""
    return JSONResponse(java_runtime.get_java_info(db))


@router.post("/java/install")
def java_install(java_version: str = Form(...), db: Session = Depends(get_db)):
    """Install one allow-listed Java major version via the privileged helper.

    The version is validated against ``ALLOWED_JAVA_VERSIONS`` BEFORE anything is
    executed; an out-of-range or non-numeric value is rejected with HTTP 400 and
    never reaches sudo. The actual run is gated again inside the service on the
    sudoers probe, so a missing rule yields a clear "GUI install is not enabled"
    result rather than a crash.
    """
    try:
        version = int(java_version)
    except (TypeError, ValueError):
        return JSONResponse(
            {"ok": False, "message": f"Invalid Java version: {java_version!r}."},
            status_code=400,
        )

    if version not in config.ALLOWED_JAVA_VERSIONS:
        allowed = "/".join(str(v) for v in config.ALLOWED_JAVA_VERSIONS)
        return JSONResponse(
            {
                "ok": False,
                "message": f"Java {version} is not allowed. Allowed: {allowed}.",
            },
            status_code=400,
        )

    result = java_runtime.install_java(version)
    # Attach the refreshed inventory so the UI can update without a second call.
    result["info"] = java_runtime.get_java_info(db)
    return JSONResponse(result)
