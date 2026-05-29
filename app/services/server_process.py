"""Start / stop / status of Minecraft server processes (v0.1, no systemd).

Servers are launched with ``subprocess.Popen`` from the web app's own user
account. The PID is persisted to the DB so status survives an app restart.

Stopping (v0.5 "server runtime controls") tries, in order, the gentlest method
that can reach the server:

  1. **RCON** ``save-all`` + ``stop`` (when RCON is enabled and reachable),
  2. **stdin** — write ``stop\\n`` to the server's console via a FIFO we wired up
     as its stdin at launch (works even though the process is detached),
  3. **SIGTERM**.

``SIGKILL`` is never sent automatically; it is only available through the
explicit, operator-confirmed :func:`kill_server` (the Kill button). A normal
stop that does not complete is reported as a failure for manual resolution.
"""
from __future__ import annotations

import errno
import os
import signal
import stat
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import psutil
from sqlalchemy.orm import Session

from app.config import (
    RCON_STOP_TIMEOUT_SECONDS,
    STDIN_STOP_TIMEOUT_SECONDS,
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


def _append_managed_log(server: Server, text: str) -> None:
    """Append a short note to the server's managed log. Never raises, never logs
    secrets — callers pass only stop-method / timing diagnostics here.
    """
    from app.services import server_console

    try:
        path = server_console.managed_log_path(server)
        with open(path, "a", encoding="utf-8") as fh:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            fh.write(f"[mc-appliance {stamp}] {text}\n")
    except (OSError, ValueError):
        pass  # logging is best-effort; never block a lifecycle action on it.


def _open_stdin_fifo(fifo_path: Path) -> Optional[int]:
    """Create (if needed) and open the stdin FIFO, returning a blocking read+write
    fd suitable for a child's stdin, or ``None`` if a FIFO can't be used.

    Opened ``O_RDWR`` so the file description always has a writer (the child
    itself), which means the server never sees an immediate EOF on stdin and its
    console reader stays alive waiting for input. ``O_NONBLOCK`` avoids the
    open() blocking for a peer; we then clear it so the child gets blocking
    semantics. A stale non-FIFO file at the path disables stdin control (we fall
    back to ``DEVNULL``) rather than deleting anything.
    """
    try:
        if fifo_path.exists():
            if not stat.S_ISFIFO(fifo_path.stat().st_mode):
                return None
        else:
            os.mkfifo(str(fifo_path), 0o600)
        fd = os.open(str(fifo_path), os.O_RDWR | os.O_NONBLOCK)
        os.set_blocking(fd, True)
        return fd
    except OSError:
        return None


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
        msg = f"Server path does not exist: {server.server_path}"
        server.last_start_error = msg[:250]
        db.commit()
        raise FileNotFoundError(msg)

    jar = server.jar_file or "server.jar"
    if not (server_dir / jar).is_file():
        msg = f"Jar file not found: {jar}"
        server.last_start_error = msg[:250]
        db.commit()
        raise FileNotFoundError(msg)

    cmd = build_start_command(server)

    # Capture stdout/stderr in a managed log file under MANAGED_LOG_DIR. The
    # filename is derived from a sanitised server name (no path traversal) — see
    # services/server_console.managed_log_path. Local import keeps the module
    # import graph acyclic (server_console imports this module).
    from app.services import server_console

    log_path = server_console.managed_log_path(server)
    log_fh = open(log_path, "ab")

    # Wire up a stdin FIFO so a later graceful stop can write "stop" to the
    # server's console. If a FIFO can't be created we simply launch with
    # DEVNULL stdin (the pre-v0.5 behaviour) and the stop path skips stdin.
    try:
        fifo_path = server_console.stdin_fifo_path(server)
    except ValueError:
        fifo_path = None
    stdin_fd = _open_stdin_fifo(fifo_path) if fifo_path is not None else None

    # Write a small banner so the managed log records *how* the server was
    # launched. The start command is only java + memory flags + the jar name —
    # it never contains the RCON password or any other secret.
    banner = (
        f"\n===== mc-appliance start {datetime.now():%Y-%m-%d %H:%M:%S} =====\n"
        f"command: {' '.join(cmd)}\n"
        f"cwd: {server_dir}\n"
    )
    try:
        log_fh.write(banner.encode("utf-8", errors="replace"))
        log_fh.flush()
    except OSError:
        pass  # The banner is best-effort; never block a start on it.

    # start_new_session detaches the child into its own process group so a
    # restart of the web app does not take the server down with it.
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(server_dir),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=(stdin_fd if stdin_fd is not None else subprocess.DEVNULL),
            start_new_session=True,
        )
    finally:
        # The child has inherited its own copy of the FIFO fd; close ours so the
        # description's writer count reflects only the child + on-demand writers.
        if stdin_fd is not None:
            try:
                os.close(stdin_fd)
            except OSError:
                pass

    # Record the PID now so the banner can name it, then append it.
    server.pid = proc.pid
    server.status = STATUS_RUNNING
    server.start_command = " ".join(cmd)
    server.last_started_at = datetime.utcnow()
    server.last_start_error = None
    server.last_stop_method = None
    server.last_stop_error = None
    server.last_startup_duration_seconds = None
    db.commit()
    try:
        log_fh.write(f"pid: {proc.pid}\n".encode("utf-8", errors="replace"))
        log_fh.flush()
    except OSError:
        pass
    return f"Server started (PID {proc.pid})."


def _mark_stopped(db: Session, server: Server, method: str, error: Optional[str] = None) -> None:
    """Persist a successful stop: clear the PID and record method / time."""
    server.status = STATUS_STOPPED
    server.pid = None
    server.last_stopped_at = datetime.utcnow()
    server.last_stop_method = method
    server.last_stop_error = (error or None)
    db.commit()


def _wait_for_exit(pid: int, server: Server, timeout: float) -> bool:
    """Poll until ``pid`` is no longer our server, or ``timeout`` elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _process_is_minecraft(pid, server):
            return True
        time.sleep(0.5)
    return not _process_is_minecraft(pid, server)


def stop_server(db: Session, server: Server) -> str:
    """Stop the server with the gentlest method that can reach it (v0.5).

    Tries, in order: RCON ``save-all`` + ``stop`` → write ``stop`` to the server
    console via its stdin FIFO → SIGTERM. SIGKILL is never sent here; if every
    graceful method fails the stop is reported as a failure for the operator to
    resolve (e.g. via the explicit Kill button). The method that worked is
    recorded on the server row and appended to the managed log.
    """
    if refresh_status(db, server) == STATUS_STOPPED:
        _append_managed_log(server, "Stop requested but server was not running.")
        return "Server is not running."

    pid = server.pid
    attempts: List[str] = []  # human-readable trace of what we tried

    # 1. RCON (only when enabled).
    if server.rcon_enabled:
        ok, note = _stop_via_rcon(db, server, pid)
        attempts.append(f"RCON: {note}")
        if ok:
            _append_managed_log(server, "Stopped by RCON (save-all + stop). " + note)
            return "Stopped by RCON (save-all + stop)."
    else:
        attempts.append("RCON: disabled for this server")

    # 2. stdin "stop" via the FIFO we attached at launch.
    ok, note = _stop_via_stdin(db, server, pid)
    attempts.append(f"stdin: {note}")
    if ok:
        _append_managed_log(server, "Stopped by stdin 'stop' command. " + note)
        return "Stopped by stdin command."

    # 3. SIGTERM (still never SIGKILL automatically).
    ok, note = _stop_via_sigterm(db, server, pid)
    attempts.append(f"SIGTERM: {note}")
    if ok:
        _append_managed_log(server, "Stopped by SIGTERM. " + note)
        # _stop_via_sigterm records the method; refine if it was already gone.
        return note if note.startswith("Server process was already gone") else "Stopped by SIGTERM."

    # Everything graceful failed.
    trace = " | ".join(attempts)
    server.last_stop_method = "failed"
    server.last_stop_error = trace[:250]
    db.commit()
    _append_managed_log(server, "Failed to stop. Tried: " + trace)
    return (
        f"Failed to stop PID {pid} (SIGKILL is not sent automatically — use the "
        f"Kill button if you must force it). Tried: {trace}"
    )


def _stop_via_rcon(db: Session, server: Server, pid: int) -> "tuple[bool, str]":
    """Attempt a graceful RCON stop. Returns (exited, note)."""
    from app.services import rcon_service

    result = rcon_service.stop_via_rcon(server)
    server.rcon_last_status = (result["message"] or "")[:250]
    db.commit()
    if not result["ok"]:
        return False, f"could not issue stop ({result['message']})"

    if _wait_for_exit(pid, server, RCON_STOP_TIMEOUT_SECONDS):
        _mark_stopped(db, server, "rcon")
        return True, "process exited after RCON stop"
    return False, f"stop issued but still alive after {RCON_STOP_TIMEOUT_SECONDS}s"


def _stop_via_stdin(db: Session, server: Server, pid: int) -> "tuple[bool, str]":
    """Write ``stop\\n`` to the server's stdin FIFO and wait for it to exit.

    Returns (exited, note). A missing FIFO, or no reader on the FIFO (the
    pre-v0.5 ``DEVNULL`` launch, or a process that closed stdin), means we cannot
    use this path and the caller falls back to SIGTERM.
    """
    from app.services import server_console

    try:
        fifo = server_console.stdin_fifo_path(server)
    except ValueError:
        return False, "stdin FIFO path invalid"
    if not fifo.exists() or not stat.S_ISFIFO(fifo.stat().st_mode):
        return False, "no stdin FIFO (server not launched with console stdin)"

    # O_WRONLY | O_NONBLOCK fails with ENXIO when there is no reader — i.e. the
    # server isn't reading its console — which tells us stdin control is moot.
    try:
        fd = os.open(str(fifo), os.O_WRONLY | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno == errno.ENXIO:
            return False, "no console reader on stdin FIFO"
        return False, f"could not open stdin FIFO ({exc})"

    try:
        os.write(fd, b"stop\n")
    except OSError as exc:
        return False, f"could not write to stdin FIFO ({exc})"
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    if _wait_for_exit(pid, server, STDIN_STOP_TIMEOUT_SECONDS):
        _mark_stopped(db, server, "stdin")
        return True, "process exited after stdin stop"
    return False, f"'stop' written but still alive after {STDIN_STOP_TIMEOUT_SECONDS}s"


def _stop_via_sigterm(db: Session, server: Server, pid: int) -> "tuple[bool, str]":
    """Send SIGTERM and wait. No SIGKILL escalation. Returns (exited, note)."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _mark_stopped(db, server, "sigterm")
        return True, "Server process was already gone; marked as stopped."
    except PermissionError:
        return False, f"permission denied sending SIGTERM to PID {pid}"

    if _wait_for_exit(pid, server, STOP_TIMEOUT_SECONDS):
        _mark_stopped(db, server, "sigterm")
        return True, "process exited after SIGTERM"
    return False, f"did not exit within {STOP_TIMEOUT_SECONDS}s of SIGTERM"


def kill_server(db: Session, server: Server) -> str:
    """Force-kill with SIGKILL — the explicit, operator-confirmed last resort.

    This is the ONLY place SIGKILL is ever sent, and it is never automatic: it
    runs only when the operator clicks the (confirmed) Kill button. It does not
    flush the world, so it can lose recent progress — hence it is kept separate
    from the graceful :func:`stop_server`.
    """
    if refresh_status(db, server) == STATUS_STOPPED:
        return "Server is not running."

    pid = server.pid
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        _mark_stopped(db, server, "sigkill")
        return "Server process was already gone; marked as stopped."
    except PermissionError:
        return f"Permission denied sending SIGKILL to PID {pid}."

    if _wait_for_exit(pid, server, STOP_TIMEOUT_SECONDS):
        _mark_stopped(db, server, "sigkill")
        _append_managed_log(server, f"Force-killed by SIGKILL (PID {pid}).")
        return "Server force-killed (SIGKILL)."

    server.last_stop_method = "failed"
    server.last_stop_error = f"SIGKILL sent to PID {pid} but it is still alive"
    db.commit()
    return f"Sent SIGKILL to PID {pid}, but it is still alive."


def restart_server(db: Session, server: Server) -> str:
    """Safe-stop, confirm stopped, then start. Aborts the start if stop failed.

    The flow is recorded in the managed log so an operator can see exactly what
    happened: stop → stopped-confirmation → start → result.
    """
    if refresh_status(db, server) == STATUS_STOPPED:
        _append_managed_log(server, "Restart: server was already stopped; starting.")
        start_msg = start_server(db, server)
        return f"Server was not running. {start_msg}"

    _append_managed_log(server, "Restart: stopping server (safe stop)…")
    stop_msg = stop_server(db, server)

    # Only proceed if the server is genuinely stopped now.
    if refresh_status(db, server) != STATUS_STOPPED:
        _append_managed_log(server, "Restart aborted: stop did not complete; not starting.")
        return f"Restart aborted (stop did not complete). {stop_msg}"

    _append_managed_log(server, "Restart: stop confirmed; starting server…")
    try:
        start_msg = start_server(db, server)
    except FileNotFoundError as exc:
        server.last_start_error = str(exc)[:250]
        db.commit()
        _append_managed_log(server, f"Restart: start failed: {exc}")
        return f"{stop_msg} Restart start FAILED: {exc}"
    _append_managed_log(server, "Restart complete.")
    return f"{stop_msg} {start_msg}"
