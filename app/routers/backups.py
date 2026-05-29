"""Backup listing and manual creation."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import STATUS_RUNNING, Backup, Server
from app.services import backup_service, rcon_service, server_process
from app.templating import templates

router = APIRouter(prefix="/backups")


@router.get("", response_class=HTMLResponse)
def list_backups(request: Request, db: Session = Depends(get_db)):
    backups = db.query(Backup).order_by(Backup.created_at.desc()).all()
    # Map server_id -> name for display.
    servers = {s.id: s.name for s in db.query(Server).all()}
    return templates.TemplateResponse(
        request,
        "backups.html",
        {"title": "Backups", "backups": backups, "servers": servers},
    )


@router.post("/create")
def create_backup(
    server_id: int = Form(...),
    exclude_logs: bool = Form(False),
    exclude_backups: bool = Form(False),
    # What to do if the pre-backup RCON save-all fails. v0.2 default is to warn
    # and continue; "abort" leaves the world un-backed-up rather than risk an
    # inconsistent snapshot.
    on_rcon_fail: str = Form("continue"),
    db: Session = Depends(get_db),
):
    server = db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")

    # If RCON is enabled and the server is running, flush the world to disk
    # first so the zip captures a consistent, fully-saved state.
    warn = ""
    server_process.refresh_status(db, server)
    if server.rcon_enabled and server.status == STATUS_RUNNING:
        save_res = rcon_service.save_all(server, flush=True)
        server.rcon_last_status = (save_res["message"] or "")[:250]
        db.commit()
        if not save_res["ok"]:
            if on_rcon_fail == "abort":
                return RedirectResponse(
                    url=f"/servers/{server_id}?msg="
                    + quote(f"Backup aborted: RCON save-all failed ({save_res['message']})."),
                    status_code=303,
                )
            warn = f"Warning: RCON save-all failed ({save_res['message']}); backed up anyway. "

    try:
        _, msg = backup_service.create_backup(
            db, server, exclude_logs=exclude_logs, exclude_backups=exclude_backups
        )
    except FileNotFoundError as exc:
        msg = f"Backup failed: {exc}"
    return RedirectResponse(
        url=f"/servers/{server_id}?msg={quote(warn + msg)}", status_code=303
    )
