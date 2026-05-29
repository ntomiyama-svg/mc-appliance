"""mc-appliance FastAPI application entrypoint.

Run with:  uvicorn app.main:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth import AuthMiddleware
from app.config import APP_NAME, APP_VERSION, BASE_DIR, SECRET_KEY
from app.database import init_db
from app.routers import auth, backups, dashboard, jars, servers, system
from app.services import scheduler_service
from app.templating import templates

app = FastAPI(title=APP_NAME, version=APP_VERSION)

# Authentication middleware (v0.1.2).
#
# Ordering note: add_middleware wraps the app from the inside out, so the LAST
# middleware added is the OUTERMOST (runs first). We need SessionMiddleware to
# run before AuthMiddleware so that request.session is populated when the auth
# check reads it — hence AuthMiddleware is added first, SessionMiddleware last.
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    same_site="lax",  # basic CSRF mitigation: cookie not sent on cross-site POSTs
    https_only=False,  # HTTPS not implemented in v0.1.2; revisit when it is
)

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
    # Start the v0.3 backup scheduler. It reads schedules from the DB on every
    # tick, so this just spins up the daemon thread; any failure is swallowed so
    # a broken scheduler can never stop the web app from serving.
    scheduler_service.start_scheduler()


# Make app metadata available to every template.
templates.env.globals["app_name"] = APP_NAME
templates.env.globals["app_version"] = APP_VERSION

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(servers.router)
app.include_router(backups.router)
app.include_router(jars.router)
app.include_router(system.router)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return templates.TemplateResponse(
        request,
        "error.html",
        {"title": "Not found", "code": 404, "detail": "Page not found"},
        status_code=404,
    )
