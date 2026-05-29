"""Read the tail of a server's logs/latest.log with path-traversal safety."""
from __future__ import annotations

from collections import deque
from pathlib import Path

from app.config import LOG_TAIL_LINES
from app.models import Server


def _safe_log_path(server: Server) -> Path:
    """Resolve logs/latest.log and confirm it stays inside the server path."""
    base = Path(server.server_path).resolve()
    candidate = (base / "logs" / "latest.log").resolve()
    # Reject anything that escapes the registered server directory.
    if base != candidate and base not in candidate.parents:
        raise ValueError("Resolved log path escapes the server directory.")
    return candidate


def read_latest_log(server: Server, lines: int = LOG_TAIL_LINES) -> str:
    """Return the last ``lines`` lines of latest.log, or a not-found message."""
    try:
        log_path = _safe_log_path(server)
    except ValueError:
        return "latest.log path is invalid."

    if not log_path.is_file():
        return "latest.log not found"

    try:
        # deque(maxlen=...) keeps memory bounded even for huge log files.
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            tail = deque(fh, maxlen=lines)
        return "".join(tail) if tail else "(latest.log is empty)"
    except OSError as exc:
        return f"Could not read latest.log: {exc}"
