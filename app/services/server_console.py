"""Server console / live-log viewer support (v0.5).

This service backs the per-server **Console** section: it locates the two log
sources we can safely show, tails them with hard bounds, and derives a
*detected state* (stopped / starting / running / crashed / unknown) from the
real process plus a scan of recent log lines.

Two log sources, deliberately kept separate (see docs/14_server_console.md):

* **latest.log** — the Minecraft server's *own* ``<server_path>/logs/latest.log``.
  Only ever read from inside the registered ``server_path`` (path-traversal
  guarded, reusing :func:`log_service._safe_log_path`).
* **managed log** — the stdout/stderr we capture when *we* launch the server,
  written under ``MANAGED_LOG_DIR``. The filename is derived from a sanitised
  server name so a crafted name can never escape that directory.

Security invariants (mirroring the rest of the app):

* No arbitrary file reads — only the two paths above, both confined to a known
  base directory.
* ``lines`` is always clamped to ``[1, CONSOLE_LOG_MAX_LINES]``.
* Nothing here runs as root, shells out, deletes files, or logs secrets.
"""
from __future__ import annotations

import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.config import (
    CONSOLE_LOG_DEFAULT_LINES,
    CONSOLE_LOG_MAX_LINES,
    CONSOLE_STATE_SCAN_LINES,
    MANAGED_LOG_DIR,
)
from app.models import (
    STATUS_CRASHED,
    STATUS_RUNNING,
    STATUS_STARTING,
    STATUS_STOPPED,
    STATUS_UNKNOWN,
    Server,
)
from app.services import log_service

# Substrings that mark a server having *finished* starting up. Any one of these
# in the recent tail is treated as "the server reached a usable state".
COMPLETION_MARKERS: Tuple[str, ...] = (
    "Done (",
    "For help, type",
    "Server thread/INFO",
)

# Substrings that look like a fault. Matched case-sensitively against the tail;
# these are the canonical forms Minecraft / the JVM actually emit.
ERROR_MARKERS: Tuple[str, ...] = (
    "Exception",
    "ERROR",
    "Failed",
    "Crash",
    "Could not",
    "Unable to",
)

# Anything outside this set in a server name is replaced before it becomes a
# managed-log filename, so the name can never introduce a path separator, "..",
# NUL, etc.
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


def clamp_lines(raw: Optional[int]) -> int:
    """Clamp a requested line count into ``[1, CONSOLE_LOG_MAX_LINES]``."""
    try:
        value = int(raw) if raw is not None else CONSOLE_LOG_DEFAULT_LINES
    except (TypeError, ValueError):
        value = CONSOLE_LOG_DEFAULT_LINES
    if value < 1:
        return 1
    if value > CONSOLE_LOG_MAX_LINES:
        return CONSOLE_LOG_MAX_LINES
    return value


def sanitize_server_name(name: str) -> str:
    """Turn an arbitrary server name into a safe managed-log basename.

    Registered (adopted) servers may carry names with characters that created
    servers reject, so we always sanitise before using a name on disk. Collapses
    runs of unsafe characters to ``_`` and falls back to ``server`` if nothing
    usable remains.
    """
    cleaned = _UNSAFE_NAME_CHARS.sub("_", (name or "").strip()).strip("_-")
    return cleaned or "server"


def managed_log_path(server: Server) -> Path:
    """Path of the managed stdout/stderr log for ``server`` under MANAGED_LOG_DIR.

    Confirms the resolved path is a direct child of ``MANAGED_LOG_DIR`` as a
    belt-and-braces check on top of name sanitisation.
    """
    base = Path(MANAGED_LOG_DIR).resolve()
    candidate = (base / f"{sanitize_server_name(server.name)}.log").resolve()
    if candidate.parent != base:
        raise ValueError("Managed log path escapes MANAGED_LOG_DIR.")
    return candidate


def _legacy_managed_log_path(server: Server) -> Path:
    """Pre-v0.5 managed-log location (``server_<id>.log``).

    Kept only so a server that is *already running* when this version ships, or
    whose log predates the rename, still shows its captured output.
    """
    return Path(MANAGED_LOG_DIR).resolve() / f"server_{server.id}.log"


def _existing_managed_log_path(server: Server) -> Optional[Path]:
    """Return the managed-log file that exists (new name first, then legacy)."""
    primary = managed_log_path(server)
    if primary.is_file():
        return primary
    legacy = _legacy_managed_log_path(server)
    if legacy.is_file():
        return legacy
    return None


def _latest_log_path(server: Server) -> Optional[Path]:
    """Return the guarded ``logs/latest.log`` path, or ``None`` if invalid."""
    try:
        return log_service._safe_log_path(server)
    except ValueError:
        return None


def _file_meta(path: Optional[Path]) -> Tuple[bool, Optional[str]]:
    """Return ``(exists, mtime_str)`` for ``path`` (never raises)."""
    if path is None:
        return False, None
    try:
        st = path.stat()
    except OSError:
        return False, None
    mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return True, mtime


def _tail_lines(path: Optional[Path], lines: int) -> List[str]:
    """Return the last ``lines`` lines of ``path`` as a list (memory-bounded)."""
    if path is None or not path.is_file():
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return list(deque(fh, maxlen=lines))
    except OSError:
        return []


def _read_log(path: Optional[Path], lines: int, missing_message: str) -> Dict:
    """Build the JSON-friendly payload for one log source."""
    exists, mtime = _file_meta(path)
    if not exists:
        return {
            "ok": True,
            "exists": False,
            "text": missing_message,
            "lines": 0,
            "mtime": None,
        }
    tail = _tail_lines(path, lines)
    text = "".join(tail) if tail else "(log file is empty)"
    return {
        "ok": True,
        "exists": True,
        "text": text,
        "lines": len(tail),
        "mtime": mtime,
    }


def read_latest_log(server: Server, lines: int) -> Dict:
    """Tail the server's own ``logs/latest.log`` (path-traversal guarded)."""
    path = _latest_log_path(server)
    if path is None:
        return {
            "ok": False,
            "exists": False,
            "text": "latest.log path is invalid.",
            "lines": 0,
            "mtime": None,
        }
    return _read_log(path, lines, "latest.log not found (server may never have started).")


def read_managed_log(server: Server, lines: int) -> Dict:
    """Tail the mc-appliance-managed stdout/stderr log for the server."""
    path = _existing_managed_log_path(server)
    return _read_log(
        path,
        lines,
        "No managed log yet — mc-appliance writes one when it starts the server.",
    )


def _scan_markers(text: str) -> Tuple[bool, bool]:
    """Return ``(has_completion, has_error)`` for a block of log text."""
    has_completion = any(marker in text for marker in COMPLETION_MARKERS)
    has_error = any(marker in text for marker in ERROR_MARKERS)
    return has_completion, has_error


def evaluate(server: Server) -> Dict:
    """Inspect process + logs and return the full console status payload.

    Does NOT mutate the DB; callers that want to reconcile the persisted status
    should call :func:`server_process.refresh_status` separately. ``db_status``
    is read straight off the row so the caller can decide ordering.
    """
    # Local import avoids any import-time cycle (server_process imports this
    # module for the managed-log path).
    from app.services import server_process

    db_status = server.status
    pid = server.pid

    try:
        process_exists = bool(pid) and server_process._process_is_minecraft(pid, server)
    except Exception:  # pragma: no cover - psutil edge cases
        process_exists = False
        actual_status = STATUS_UNKNOWN
    else:
        actual_status = STATUS_RUNNING if process_exists else STATUS_STOPPED

    latest_path = _latest_log_path(server)
    managed_path = _existing_managed_log_path(server)
    latest_exists, latest_mtime = _file_meta(latest_path)
    managed_exists, managed_mtime = _file_meta(managed_path)

    # Scan the recent tail of both logs for the marker words.
    scan_text = "".join(
        _tail_lines(latest_path, CONSOLE_STATE_SCAN_LINES)
        + _tail_lines(managed_path, CONSOLE_STATE_SCAN_LINES)
    )
    has_completion, has_error = _scan_markers(scan_text)

    detected_state, detected_reason = _classify(
        db_status=db_status,
        process_exists=process_exists,
        actual_status=actual_status,
        has_completion=has_completion,
        has_error=has_error,
    )

    return {
        "db_status": db_status,
        "actual_status": actual_status,
        "pid": pid,
        "process_exists": process_exists,
        "latest_log_exists": latest_exists,
        "managed_log_exists": managed_exists,
        "latest_log_mtime": latest_mtime,
        "managed_log_mtime": managed_mtime,
        "detected_state": detected_state,
        "detected_reason": detected_reason,
    }


def _classify(
    db_status: str,
    process_exists: bool,
    actual_status: str,
    has_completion: bool,
    has_error: bool,
) -> Tuple[str, str]:
    """Pure state machine for the detected state (see docs/14_server_console)."""
    if actual_status == STATUS_UNKNOWN:
        return STATUS_UNKNOWN, "Could not determine the process state."

    if not process_exists:
        # No live process. If the DB still thinks it should be up, that's an
        # unexpected exit; if recent logs carry faults, call it crashed.
        if db_status in (STATUS_RUNNING, STATUS_STARTING):
            return (
                STATUS_CRASHED,
                "Database shows the server running but no live process was found "
                "(unexpected exit).",
            )
        if has_error:
            return (
                STATUS_CRASHED,
                "No live process and error/exception lines are present in the logs.",
            )
        return STATUS_STOPPED, "No PID, or the process is not running."

    # Process is alive.
    if has_completion:
        return (
            STATUS_RUNNING,
            "Process is alive and a startup-complete line was found in the logs.",
        )
    if has_error:
        return (
            STATUS_CRASHED,
            "Process is alive but error lines appear before any startup-complete "
            "marker (possible failed startup).",
        )
    return (
        STATUS_STARTING,
        "Process is alive but no startup-complete line has appeared yet.",
    )
