"""mc-appliance FastAPI application entrypoint.

Run with:  uvicorn app.main:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import APP_NAME, APP_VERSION, BASE_DIR
from app.database import init_db
from app.routers import backups, dashboard, servers, system
from app.templating import templates

app = FastAPI(title=APP_NAME, version=APP_VERSION)

# Static assets (CSS / JS).
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "app" / "static")),
    name="static",
)


@app.on_event("startup")
def _on_startup() -> None:
    init_db()
    # Refuse to run as root: the web app must not be root in v0.1.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise RuntimeError(
            "mc-appliance must not be run as root. Start it as a normal user."
        )


# Make app metadata available to every template.
templates.env.globals["app_name"] = APP_NAME
templates.env.globals["app_version"] = APP_VERSION

app.include_router(dashboard.router)
app.include_router(servers.router)
app.include_router(backups.router)
app.include_router(system.router)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return templates.TemplateResponse(
        request,
        "error.html",
        {"title": "Not found", "code": 404, "detail": "Page not found"},
        status_code=404,
    )
