"""Authentication for v0.1.2: password hashing, session helpers and the
login-enforcement middleware.

This is intentionally a *single-admin* model — credentials live in the
``admins`` table and a successful login stores the admin id/username in the
signed session cookie. There is no role system, signup or password-reset UI
yet; those are deferred to a later version (see docs/08_authentication.md).
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from passlib.context import CryptContext
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from app.models import Admin

# bcrypt password hashing. ``deprecated="auto"`` lets us transparently upgrade
# the hashing scheme in a future version without breaking existing hashes.
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Keys under which the logged-in admin is stored in request.session.
SESSION_USER_ID = "admin_id"
SESSION_USERNAME = "admin_username"

# Paths reachable WITHOUT authentication. Everything else requires a login.
_PUBLIC_PATHS = {"/login", "/logout"}
_PUBLIC_PREFIXES = ("/static/",)


def hash_password(password: str) -> str:
    """Return a bcrypt hash for ``password`` (never store the plaintext)."""
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Check a plaintext ``password`` against a stored bcrypt hash."""
    try:
        return _pwd_context.verify(password, password_hash)
    except ValueError:
        # Malformed/empty hash in the DB — treat as a failed login, not a 500.
        return False


def authenticate(db: Session, username: str, password: str) -> Optional[Admin]:
    """Return the matching admin if credentials are valid, else ``None``."""
    admin = db.query(Admin).filter(Admin.username == username).first()
    if admin is None:
        # Still run a verify against a dummy to reduce username-enumeration via
        # timing; cheap and harmless for a single-admin appliance.
        verify_password(password, "$2b$12$" + "." * 53)
        return None
    if not verify_password(password, admin.password_hash):
        return None
    return admin


def login_session(request: Request, admin: Admin) -> None:
    """Mark the current session as authenticated for ``admin``."""
    request.session[SESSION_USER_ID] = admin.id
    request.session[SESSION_USERNAME] = admin.username


def logout_session(request: Request) -> None:
    """Clear authentication state from the session."""
    request.session.pop(SESSION_USER_ID, None)
    request.session.pop(SESSION_USERNAME, None)


def is_authenticated(request: Request) -> bool:
    return request.session.get(SESSION_USER_ID) is not None


def current_username(request: Request) -> Optional[str]:
    return request.session.get(SESSION_USERNAME)


def _is_public(path: str) -> bool:
    if path in _PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    """Require a logged-in admin for every route except the public allowlist.

    Unauthenticated requests are handled by their type:

    * ``/api/*``  -> 401 JSON (so fetch()/AJAX callers see an auth error)
    * everything else (HTML pages and form POSTs) -> 303 redirect to /login

    This middleware must be installed *inside* Starlette's ``SessionMiddleware``
    so that ``request.session`` is already populated when we read it (see
    app/main.py for the ordering).
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if _is_public(path) or is_authenticated(request):
            return await call_next(request)

        if path.startswith("/api/"):
            return JSONResponse(
                {"detail": "Authentication required"}, status_code=401
            )

        # HTML pages / form actions: bounce to the login screen, remembering
        # where the user was headed so we can return there after login.
        next_url = path
        if request.url.query:
            next_url = f"{path}?{request.url.query}"
        return RedirectResponse(url=f"/login?next={quote(next_url)}", status_code=303)
