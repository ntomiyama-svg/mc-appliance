"""Backup listing and manual creation."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Backup, Server
from app.services import backup_service
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
    db: Session = Depends(get_db),
):
    server = db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    try:
        _, msg = backup_service.create_backup(
            db, server, exclude_logs=exclude_logs, exclude_backups=exclude_backups
        )
    except FileNotFoundError as exc:
        msg = f"Backup failed: {exc}"
    return RedirectResponse(
        url=f"/servers/{server_id}?msg={quote(msg)}", status_code=303
    )
