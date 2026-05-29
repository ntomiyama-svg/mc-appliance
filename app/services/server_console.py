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
    STATUS_FAILED_TO_START,
    STATUS_RUNNING,
    STATUS_STARTING,
    STATUS_STOPPED,
    STATUS_STOPPING,
    STATUS_UNKNOWN,
    Server,
)
from app.services import log_service

# Substrings that mark a server having *finished* starting up. The canonical
# signal is the "Done (12.345s)!" line; we keep the help banner as a backup.
# (We deliberately do NOT treat generic "Server thread/INFO" lines as
# completion — they appear throughout a run and would mask the starting state.)
COMPLETION_MARKERS: Tuple[str, ...] = (
    "Done (",
    'For help, type "help"',
)

# Parses the startup duration out of the canonical line: ``Done (12.345s)! ...``
_DONE_RE = re.compile(r"Done \(([\d.]+)s\)")

# Markers that the server is in the middle of a *graceful shutdown*. Kept narrow
# (the explicit "Stopping …" lines) so routine autosave chatter like
# "Saving chunks" does not get misread as stopping while the server is running.
STOP_MARKERS: Tuple[str, ...] = (
    "Stopping the server",
    "Stopping server",
)

# Specific, high-signal fault lines mapped to a short operator-facing summary.
# Order matters: the first match wins as the "last error summary". These are the
# canonical forms Minecraft / the JVM actually emit. When one of these appears
# without a startup-complete line, the server is classified failed_to_start.
SPECIFIC_ERROR_MARKERS: Tuple[Tuple[str, str], ...] = (
    ("UnsupportedClassVersionError",
     "Java is too old for this server (UnsupportedClassVersionError) — select a newer Java runtime."),
    ("You need to agree to the EULA",
     "EULA not accepted — set eula=true (Server jar & EULA)."),
    ("Failed to load eula.txt",
     "Could not read eula.txt."),
    ("FAILED TO BIND TO PORT",
     "Could not bind the server port — it may already be in use."),
    ("Address already in use",
     "The server port is already in use (Address already in use)."),
    ("OutOfMemoryError",
     "The server ran out of memory — increase -Xmx (max memory)."),
    ("Permission denied",
     "Permission denied accessing a file in the server directory."),
)

# Broad fallback fault markers (case-sensitive) used only to flag a generic
# error when none of the specific markers above matched.
GENERIC_ERROR_MARKERS: Tuple[str, ...] = (
    "Exception",
    "ERROR",
    "Crash",
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


def stdin_fifo_path(server: Server) -> Path:
    """Path of the stdin FIFO used to send console input to ``server``.

    Lives next to the managed log under MANAGED_LOG_DIR, with the same sanitised
    basename, so it inherits the identical path-traversal guarantee. We launch
    the server with this FIFO as its stdin (see ``server_process.start_server``)
    so a graceful ``stop`` can be written to the running console even though the
    process is detached and our original ``Popen`` handle is long gone.
    """
    base = Path(MANAGED_LOG_DIR).resolve()
    candidate = (base / f"{sanitize_server_name(server.name)}.stdin").resolve()
    if candidate.parent != base:
        raise ValueError("Managed stdin FIFO path escapes MANAGED_LOG_DIR.")
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


def _scan_markers(text: str) -> Dict:
    """Scan a block of log text for the state-driving markers.

    Returns a dict with: has_completion, has_stop, specific_error (summary or
    None), has_generic_error, startup_duration (float seconds, or None).
    """
    has_completion = any(marker in text for marker in COMPLETION_MARKERS)
    has_stop = any(marker in text for marker in STOP_MARKERS)

    specific_error: Optional[str] = None
    for needle, summary in SPECIFIC_ERROR_MARKERS:
        if needle in text:
            specific_error = summary
            break
    has_generic_error = specific_error is not None or any(
        marker in text for marker in GENERIC_ERROR_MARKERS
    )

    startup_duration: Optional[float] = None
    matches = _DONE_RE.findall(text)
    if matches:
        try:
            startup_duration = float(matches[-1])
        except ValueError:
            startup_duration = None

    return {
        "has_completion": has_completion,
        "has_stop": has_stop,
        "specific_error": specific_error,
        "has_generic_error": has_generic_error,
        "startup_duration": startup_duration,
    }


def _server_port(server: Server) -> Optional[int]:
    """Best-effort: the server's listen port from server.properties (or None)."""
    try:
        from app.services import properties_service

        props, exists = properties_service.read_properties(server)
        if not exists:
            return None
        raw = props.get("server-port")
        return int(raw) if raw not in (None, "") else None
    except (ValueError, OSError):
        return None


def _port_listening(port: Optional[int]) -> Optional[bool]:
    """Whether something is LISTENing on ``port`` (None if it can't be checked)."""
    if not port:
        return None
    try:
        import psutil

        for conn in psutil.net_connections(kind="inet"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port:
                return True
        return False
    except Exception:  # pragma: no cover - psutil perms / platform differences
        return None


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
    markers = _scan_markers(scan_text)

    port = _server_port(server)
    port_listening = _port_listening(port)

    detected_state, detected_reason = _classify(
        db_status=db_status,
        process_exists=process_exists,
        actual_status=actual_status,
        markers=markers,
        port_listening=port_listening,
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
        # Richer signals (v0.5).
        "server_port": port,
        "port_listening": port_listening,
        "last_error_summary": markers["specific_error"],
        "startup_duration_seconds": markers["startup_duration"],
        # Persisted bookkeeping echoed for the UI (set by server_process).
        "last_stop_method": server.last_stop_method,
        "last_started_at": _fmt_dt(server.last_started_at),
        "last_stopped_at": _fmt_dt(server.last_stopped_at),
        "last_start_error": server.last_start_error,
        "last_stop_error": server.last_stop_error,
    }


def _fmt_dt(value) -> Optional[str]:
    """Format a datetime for JSON, or None."""
    try:
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else None
    except AttributeError:
        return None


def _classify(
    db_status: str,
    process_exists: bool,
    actual_status: str,
    markers: Dict,
    port_listening: Optional[bool],
) -> Tuple[str, str]:
    """Pure state machine for the detected state (see docs/14_server_console)."""
    if actual_status == STATUS_UNKNOWN:
        return STATUS_UNKNOWN, "Could not determine the process state."

    has_completion = markers["has_completion"]
    has_stop = markers["has_stop"]
    specific_error = markers["specific_error"]
    has_error = markers["has_generic_error"]

    if not process_exists:
        # No live process.
        if specific_error and not has_completion:
            return STATUS_FAILED_TO_START, f"Server failed to start: {specific_error}"
        if has_error and not has_completion:
            return (
                STATUS_FAILED_TO_START,
                "No live process and error lines appear before any startup-complete "
                "marker (startup likely failed).",
            )
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
    if has_stop and not has_completion:
        return STATUS_STOPPING, "Process is alive and a 'Stopping …' line was found."
    if has_stop:
        # Completion seen earlier AND a stop line now → it ran and is shutting down.
        return STATUS_STOPPING, "Process is alive and shutting down ('Stopping …')."
    if has_completion:
        port_note = ""
        if port_listening is True:
            port_note = " The server port is listening."
        elif port_listening is False:
            port_note = " (Startup complete but the server port is not yet listening.)"
        return (
            STATUS_RUNNING,
            "Process is alive and a startup-complete line was found." + port_note,
        )
    if specific_error:
        return STATUS_FAILED_TO_START, f"Startup error before completion: {specific_error}"
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
