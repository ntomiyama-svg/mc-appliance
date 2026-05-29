"""Server list, registration, detail and lifecycle actions."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import DEFAULT_JAVA_PATH, DEFAULT_MAX_MEMORY, DEFAULT_MIN_MEMORY
from app.database import get_db
from app.models import Server
from app.services import (
    log_service,
    properties_service,
    server_detector,
    server_process,
)
from app.templating import templates

router = APIRouter(prefix="/servers")


def _get_server_or_404(db: Session, server_id: int) -> Server:
    server = db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


@router.get("", response_class=HTMLResponse)
def list_servers(request: Request, db: Session = Depends(get_db)):
    servers = db.query(Server).order_by(Server.name).all()
    for server in servers:
        server_process.refresh_status(db, server)
    return templates.TemplateResponse(
        request, "servers.html", {"title": "Servers", "servers": servers}
    )


@router.get("/register", response_class=HTMLResponse)
def register_form(
    request: Request,
    path: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Show the registration form. If ``path`` is given, run detection on it."""
    detection = None
    if path:
        detection = server_detector.detect_server(path)
    return templates.TemplateResponse(
        request,
        "server_register.html",
        {
            "title": "Register Server",
            "detection": detection,
            "path": path or "",
            "defaults": {
                "java_path": DEFAULT_JAVA_PATH,
                "min_memory": DEFAULT_MIN_MEMORY,
                "max_memory": DEFAULT_MAX_MEMORY,
            },
        },
    )


@router.post("/register")
def register_server(
    request: Request,
    name: str = Form(...),
    server_path: str = Form(...),
    java_path: str = Form(DEFAULT_JAVA_PATH),
    min_memory: str = Form(DEFAULT_MIN_MEMORY),
    max_memory: str = Form(DEFAULT_MAX_MEMORY),
    jar_file: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    server_path = server_path.strip()
    name = name.strip()
    base = Path(server_path)

    errors = []
    if not name:
        errors.append("Name is required.")
    if not base.is_dir():
        errors.append(f"Server path is not a directory: {server_path}")
    if db.query(Server).filter(Server.name == name).first():
        errors.append(f"A server named '{name}' is already registered.")

    detection = server_detector.detect_server(server_path) if base.is_dir() else None

    # jar_file must be one of the detected candidates under server_path.
    chosen_jar = (jar_file or "").strip() or None
    if detection and detection.jar_candidates:
        if chosen_jar is None:
            chosen_jar = detection.jar_candidates[0]
        elif chosen_jar not in detection.jar_candidates:
            errors.append("Selected jar is not located inside the server path.")
    elif detection is not None and not detection.jar_candidates:
        errors.append("No .jar file was found in the server path.")

    if errors:
        return templates.TemplateResponse(
            request,
            "server_register.html",
            {
                "title": "Register Server",
                "detection": detection,
                "path": server_path,
                "errors": errors,
                "form": {
                    "name": name,
                    "java_path": java_path,
                    "min_memory": min_memory,
                    "max_memory": max_memory,
                },
                "defaults": {
                    "java_path": DEFAULT_JAVA_PATH,
                    "min_memory": DEFAULT_MIN_MEMORY,
                    "max_memory": DEFAULT_MAX_MEMORY,
                },
            },
            status_code=400,
        )

    server = Server(
        name=name,
        server_path=str(base.resolve()),
        jar_file=chosen_jar,
        java_path=java_path.strip() or DEFAULT_JAVA_PATH,
        min_memory=min_memory.strip() or DEFAULT_MIN_MEMORY,
        max_memory=max_memory.strip() or DEFAULT_MAX_MEMORY,
        minecraft_type=detection.minecraft_type if detection else "Unknown",
        minecraft_version=detection.minecraft_version if detection else None,
        has_mods=detection.has_mods if detection else False,
        has_plugins=detection.has_plugins if detection else False,
    )
    server.start_command = " ".join(server_process.build_start_command(server))
    db.add(server)
    db.commit()
    db.refresh(server)

    return RedirectResponse(url=f"/servers/{server.id}", status_code=303)


@router.get("/{server_id}", response_class=HTMLResponse)
def server_detail(server_id: int, request: Request, db: Session = Depends(get_db)):
    server = _get_server_or_404(db, server_id)
    server_process.refresh_status(db, server)
    detection = server_detector.detect_server(server.server_path)
    log_text = log_service.read_latest_log(server)
    start_command = " ".join(server_process.build_start_command(server))
    return templates.TemplateResponse(
        request,
        "server_detail.html",
        {
            "title": server.name,
            "server": server,
            "detection": detection,
            "log_text": log_text,
            "start_command": start_command,
        },
    )


@router.get("/{server_id}/log/raw", response_class=HTMLResponse)
def server_log_raw(server_id: int, db: Session = Depends(get_db)):
    """Plain-text latest.log tail for AJAX refresh."""
    server = _get_server_or_404(db, server_id)
    return HTMLResponse(content=log_service.read_latest_log(server), media_type="text/plain")


def _action_redirect(server_id: int, message: str) -> RedirectResponse:
    # Pass a short status message back via query string for display.
    from urllib.parse import quote

    return RedirectResponse(
        url=f"/servers/{server_id}?msg={quote(message)}", status_code=303
    )


@router.get("/{server_id}/properties", response_class=HTMLResponse)
def properties_form(server_id: int, request: Request, db: Session = Depends(get_db)):
    server = _get_server_or_404(db, server_id)
    props, exists = properties_service.read_properties(server)
    return templates.TemplateResponse(
        request,
        "properties.html",
        {
            "title": f"{server.name} · properties",
            "server": server,
            "props": props,
            "exists": exists,
        },
    )


@router.post("/{server_id}/properties")
async def properties_save(server_id: int, request: Request, db: Session = Depends(get_db)):
    server = _get_server_or_404(db, server_id)
    form = await request.form()

    # Only persist keys that already exist in the file; ignore unknown input.
    existing, exists = properties_service.read_properties(server)
    if not exists:
        return _action_redirect(server_id, "server.properties not found; nothing saved.")

    new_values = dict(existing)
    for key in existing:
        field = f"prop__{key}"
        if field in form:
            new_values[key] = str(form[field])

    properties_service.write_properties(server, new_values)
    from urllib.parse import quote

    return RedirectResponse(
        url=f"/servers/{server_id}/properties?msg={quote('Saved (.bak created).')}",
        status_code=303,
    )


@router.post("/{server_id}/start")
def action_start(server_id: int, db: Session = Depends(get_db)):
    server = _get_server_or_404(db, server_id)
    try:
        msg = server_process.start_server(db, server)
    except FileNotFoundError as exc:
        msg = f"Start failed: {exc}"
    return _action_redirect(server_id, msg)


@router.post("/{server_id}/stop")
def action_stop(server_id: int, db: Session = Depends(get_db)):
    server = _get_server_or_404(db, server_id)
    msg = server_process.stop_server(db, server)
    return _action_redirect(server_id, msg)


@router.post("/{server_id}/restart")
def action_restart(server_id: int, db: Session = Depends(get_db)):
    server = _get_server_or_404(db, server_id)
    try:
        msg = server_process.restart_server(db, server)
    except FileNotFoundError as exc:
        msg = f"Restart failed: {exc}"
    return _action_redirect(server_id, msg)
