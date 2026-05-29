"""System information page."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.services import system_metrics
from app.templating import templates

router = APIRouter(prefix="/system")


@router.get("", response_class=HTMLResponse)
def system_page(request: Request):
    return templates.TemplateResponse(
        request,
        "system.html",
        {"title": "System", "info": system_metrics.get_system_info()},
    )
