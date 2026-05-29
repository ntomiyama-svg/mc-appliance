"""Start / stop / status of Minecraft server processes (v0.1, no systemd).

Servers are launched with ``subprocess.Popen`` from the web app's own user
account. The PID is persisted to the DB so status survives an app restart.
We deliberately avoid SIGKILL in v0.1; a stop that does not complete within
the timeout is reported as a failure for the operator to resolve manually.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import List, Optional

import psutil
from sqlalchemy.orm import Session

from app.config import (
    MANAGED_LOG_DIR,
    RCON_STOP_TIMEOUT_SECONDS,
    STOP_TIMEOUT_SECONDS,
)
from app.models import STATUS_RUNNING, STATUS_STOPPED, Server


def build_start_command(server: Server) -> List[str]:
    """Build the java argv list for a server, e.g.

    ``[java, -Xms1G, -Xmx4G, -jar, server.jar, nogui]``
    """
    return [
        server.java_path,
        f"-Xms{server.min_memory}",
        f"-Xmx{server.max_memory}",
        "-jar",
        server.jar_file or "server.jar",
        "nogui",
    ]


def _process_is_minecraft(pid: int, server: Server) -> bool:
    """Confirm the PID is alive and looks like *our* server, not a recycled PID."""
    try:
        proc = psutil.Process(pid)
        if not proc.is_running():
            return False
        if proc.status() == psutil.STATUS_ZOMBIE:
            return False
        # Best-effort sanity check: cwd should match the server path.
        try:
            return Path(proc.cwd()).resolve() == Path(server.server_path).resolve()
        except (psutil.AccessDenied, psutil.Error):
            # Can't read cwd (e.g. permissions) — fall back to "alive" only.
            return True
    except (psutil.NoSuchProcess, psutil.Error):
        return False


def refresh_status(db: Session, server: Server) -> str:
    """Reconcile DB status with the real process state and return the status."""
    if server.pid and _process_is_minecraft(server.pid, server):
        if server.status != STATUS_RUNNING:
            server.status = STATUS_RUNNING
            db.commit()
        return STATUS_RUNNING

    # No live process: ensure we are marked stopped and clear the stale PID.
    if server.status != STATUS_STOPPED or server.pid is not None:
        server.status = STATUS_STOPPED
        server.pid = None
        db.commit()
    return STATUS_STOPPED


def start_server(db: Session, server: Server) -> str:
    """Start the server process. Returns a human-readable status message."""
    if refresh_status(db, server) == STATUS_RUNNING:
        return "Server is already running."

    server_dir = Path(server.server_path)
    if not server_dir.is_dir():
        raise FileNotFoundError(f"Server path does not exist: {server.server_path}")

    jar = server.jar_file or "server.jar"
    if not (server_dir / jar).is_file():
        raise FileNotFoundError(f"Jar file not found: {jar}")

    cmd = build_start_command(server)

    # Capture stdout/stderr in a managed log file inside data/server_logs/.
    log_path = MANAGED_LOG_DIR / f"server_{server.id}.log"
    log_fh = open(log_path, "ab")

    # start_new_session detaches the child into its own process group so a
    # restart of the web app does not take the server down with it.
    proc = subprocess.Popen(
        cmd,
        cwd=str(server_dir),
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )

    server.pid = proc.pid
    server.status = STATUS_RUNNING
    server.start_command = " ".join(cmd)
    db.commit()
    return f"Server started (PID {proc.pid})."


def stop_server(db: Session, server: Server) -> str:
    """Stop the server, preferring a graceful RCON shutdown (v0.2).

    Order of operations:

    1. If RCON is enabled, issue ``save-all flush`` + ``stop`` over RCON and wait
       up to ``RCON_STOP_TIMEOUT_SECONDS`` for the process to exit.
    2. If RCON is disabled, cannot be reached, or the process does not exit in
       time, fall back to the v0.1 SIGTERM path.

    SIGKILL is still never sent automatically.
    """
    if refresh_status(db, server) == STATUS_STOPPED:
        return "Server is not running."

    pid = server.pid

    if server.rcon_enabled:
        rcon_msg = _stop_via_rcon(db, server, pid)
        if rcon_msg is not None:
            return rcon_msg
        # RCON unavailable or the process outlived the RCON wait: fall back.
        fallback = _stop_via_sigterm(db, server, pid)
        return f"RCON stop unavailable/incomplete; used SIGTERM. {fallback}"

    return _stop_via_sigterm(db, server, pid)


def _stop_via_rcon(db: Session, server: Server, pid: int) -> Optional[str]:
    """Attempt a graceful RCON stop.

    Returns a status message if the process exited within the RCON timeout, or
    ``None`` to signal the caller to fall back to SIGTERM (RCON could not be used
    or the process did not exit in time).
    """
    # Local import keeps the module import graph simple (rcon_service does not
    # import server_process, so there is no real cycle, but this is tidy).
    from app.services import rcon_service

    result = rcon_service.stop_via_rcon(server)
    server.rcon_last_status = (result["message"] or "")[:250]
    db.commit()
    if not result["ok"]:
        return None  # Could not even issue the RCON stop — fall back.

    deadline = time.time() + RCON_STOP_TIMEOUT_SECONDS
    while time.time() < deadline:
        if not _process_is_minecraft(pid, server):
            server.status = STATUS_STOPPED
            server.pid = None
            db.commit()
            return "Server stopped via RCON (save-all + stop)."
        time.sleep(0.5)

    return None  # Stop issued but still alive — fall back to SIGTERM.


def _stop_via_sigterm(db: Session, server: Server, pid: int) -> str:
    """v0.1 fallback: send SIGTERM and wait. No SIGKILL escalation."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        server.status = STATUS_STOPPED
        server.pid = None
        db.commit()
        return "Server process was already gone; marked as stopped."
    except PermissionError:
        return f"Permission denied sending SIGTERM to PID {pid}."

    # Wait up to the timeout for the process to exit.
    deadline = time.time() + STOP_TIMEOUT_SECONDS
    while time.time() < deadline:
        if not _process_is_minecraft(pid, server):
            server.status = STATUS_STOPPED
            server.pid = None
            db.commit()
            return "Server stopped (SIGTERM)."
        time.sleep(0.5)

    # Still alive: report failure. Operator can stop manually; SIGKILL is never
    # performed automatically.
    return (
        f"Stop FAILED: PID {pid} did not exit within {STOP_TIMEOUT_SECONDS}s. "
        "SIGKILL is not performed automatically."
    )


def restart_server(db: Session, server: Server) -> str:
    """Stop then start. Aborts the start if the stop did not succeed."""
    stop_msg = stop_server(db, server)
    if "FAILED" in stop_msg:
        return f"Restart aborted. {stop_msg}"
    start_msg = start_server(db, server)
    return f"{stop_msg} {start_msg}"
