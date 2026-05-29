"""Dashboard and System pages."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import VANILLA_AUTO_CHECK_ENABLED
from app.database import get_db
from app.models import STATUS_RUNNING, BackupSchedule, Server
from app.services import server_process, system_metrics, vanilla_downloader
from app.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    servers = db.query(Server).all()
    # Reconcile each server's status against the real process state.
    running = 0
    for server in servers:
        if server_process.refresh_status(db, server) == STATUS_RUNNING:
            running += 1

    metrics = system_metrics.get_metrics()
    schedules_enabled = (
        db.query(BackupSchedule).filter(BackupSchedule.enabled.is_(True)).count()
    )

    # Vanilla jar-cache warning (v0.4). Best-effort and non-fatal: a failed
    # external check (offline host) must never bring the dashboard down, so the
    # helper swallows all errors and returns None.
    vanilla_warning = None
    if VANILLA_AUTO_CHECK_ENABLED:
        vanilla_warning = vanilla_downloader.latest_cache_warning(db)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "title": "Dashboard",
            "server_count": len(servers),
            "running_count": running,
            "schedules_enabled": schedules_enabled,
            "metrics": metrics,
            "vanilla_warning": vanilla_warning,
        },
    )


@router.get("/api/metrics")
def api_metrics():
    """JSON metrics endpoint for live dashboard refresh."""
    return system_metrics.get_metrics()
