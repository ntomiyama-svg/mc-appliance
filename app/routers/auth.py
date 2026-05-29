"""Login / logout routes (v0.1.2 single-admin auth)."""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import auth
from app.database import get_db
from app.templating import templates

router = APIRouter()


def _safe_next(next_url: Optional[str]) -> str:
    """Sanitise the post-login redirect target to avoid open redirects.

    Only same-site, absolute-path URLs are honoured; anything else (external
    host, scheme, or a bounce back to the auth pages) falls back to "/".
    """
    if not next_url or not next_url.startswith("/"):
        return "/"
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return "/"
    if next_url.startswith("/login") or next_url.startswith("/logout"):
        return "/"
    return next_url


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: Optional[str] = None):
    # Already signed in? Skip the form.
    if auth.is_authenticated(request):
        return RedirectResponse(url=_safe_next(next), status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"title": "Sign in", "next": next or "", "error": None},
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    admin = auth.authenticate(db, username.strip(), password)
    if admin is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "title": "Sign in",
                "next": next,
                "error": "Invalid username or password.",
            },
            status_code=401,
        )
    auth.login_session(request, admin)
    return RedirectResponse(url=_safe_next(next), status_code=303)


@router.get("/logout")
def logout(request: Request):
    auth.logout_session(request)
    return RedirectResponse(url="/login", status_code=303)
