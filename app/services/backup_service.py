"""Backups for a registered server.

Two mechanisms live here:

* **v0.3 zip backups** (:func:`create_backup`) — a quick, while-running zip of
  the server directory (RCON ``save-all`` is done by the caller first). Kept for
  backward compatibility.
* **v0.6 rsync hardlink snapshots** (:func:`backup_now`, :func:`restore_backup`,
  :func:`delete_backup`, generation retention) — the hardened path. A backup
  always **stops** a running server first for a consistent snapshot and only
  restarts it if it had been running; a server that cannot be stopped aborts the
  backup (we never SIGKILL). See docs/18_backup_restore.md.

Safety model: every snapshot/restore path is validated to stay within
``BACKUP_DIR`` (snapshots) or the registered ``server_path`` (restore target).
File deletion is limited to *backup snapshots under BACKUP_DIR* (retention
pruning + the explicit Delete action) — registered server files are never
deleted by this module; a restore replaces them via rsync, and a mandatory
pre-restore snapshot is always taken first.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.config import (
    BACKUP_DAILY_DEDUP_HOURS,
    BACKUP_DIR,
    BACKUP_TIMESTAMP_FMT,
    RSYNC_TIMEOUT_SECONDS,
)
from app.models import (
    Backup,
    Server,
    BACKUP_FAILED,
    BACKUP_IN_PROGRESS,
    BACKUP_METHOD_RSYNC,
    BACKUP_METHOD_ZIP,
    BACKUP_OK,
    BACKUP_TYPE_NORMAL,
    BACKUP_TYPE_PRE_RESTORE,
    STATUS_RUNNING,
    STATUS_STOPPED,
)

# Directory names that are excluded from backups by default.
DEFAULT_EXCLUDE_DIRS = {"logs", "backups", "crash-reports"}


def _safe_name(name: str) -> str:
    """Sanitize a server name for use as a directory component."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "server"


def create_backup(
    db: Session,
    server: Server,
    exclude_logs: bool = True,
    exclude_backups: bool = True,
    timestamp: str = None,
) -> Tuple[Backup, str]:
    """Zip ``server.server_path`` into backups/<name>/<timestamp>.zip.

    Returns the created Backup row and a status message. The backup is confined
    to files under the registered server path (no traversal outside it).
    """
    base = Path(server.server_path).resolve()
    if not base.is_dir():
        raise FileNotFoundError(f"Server path does not exist: {server.server_path}")

    excludes = set()
    if exclude_logs:
        excludes.add("logs")
    if exclude_backups:
        excludes.add("backups")
    excludes |= {"crash-reports"}

    if timestamp is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    dest_dir = BACKUP_DIR / _safe_name(server.name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_name = f"{timestamp}.zip"
    zip_path = dest_dir / zip_name

    file_count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in base.rglob("*"):
            # Skip excluded top-level directories.
            try:
                rel = path.relative_to(base)
            except ValueError:
                continue
            if rel.parts and rel.parts[0] in excludes:
                continue
            # Confirm the real path stays inside base (guards against symlinks).
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if base != resolved and base not in resolved.parents:
                continue
            if path.is_file():
                zf.write(path, rel.as_posix())
                file_count += 1

    size = zip_path.stat().st_size
    backup = Backup(
        server_id=server.id,
        backup_name=zip_name,
        backup_path=str(zip_path),
        size_bytes=size,
        method=BACKUP_METHOD_ZIP,
        backup_type=BACKUP_TYPE_NORMAL,
        status=BACKUP_OK,
    )
    db.add(backup)
    db.commit()
    db.refresh(backup)

    return backup, f"Backup created: {zip_name} ({file_count} files, {size} bytes)."


# =============================================================================
# v0.6 rsync hardlink snapshots, generation retention, restore
# =============================================================================


class BackupOutcome:
    """Result of a stop-aware backup attempt."""

    def __init__(
        self,
        ok: bool,
        backup: Optional[Backup] = None,
        message: str = "",
        aborted: bool = False,
    ) -> None:
        self.ok = ok
        self.backup = backup
        self.message = message
        # ``aborted`` means we never reached the snapshot (e.g. a running server
        # could not be stopped), as opposed to the snapshot itself failing.
        self.aborted = aborted


class StartGate:
    """Decision returned by the backup-on-start hook."""

    def __init__(self, allow_start: bool, message: str = "", level: str = "info") -> None:
        self.allow_start = allow_start
        self.message = message
        self.level = level


class RestoreOutcome:
    def __init__(self, ok: bool, message: str = "", level: str = "info") -> None:
        self.ok = ok
        self.message = message
        self.level = level


def _append_managed_log(server: Server, text: str) -> None:
    """Append a line to the server's managed log (best-effort, never raises).

    Mirrors ``server_process._append_managed_log`` so backup activity shows up in
    the same Console "managed log" view as start/stop activity.
    """
    from app.services import server_console

    try:
        path = server_console.managed_log_path(server)
        with open(path, "a", encoding="utf-8") as fh:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            fh.write(f"[mc-appliance backup {stamp}] {text}\n")
    except (OSError, ValueError):
        pass


def _fmt_size(num: Optional[int]) -> str:
    if not num:
        return "0 B"
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def _within(base: Path, target: Path) -> bool:
    """True if ``target`` is inside (or equal to) ``base`` after resolving."""
    base_r = base.resolve()
    target_r = target.resolve()
    return target_r == base_r or base_r in target_r.parents


def _server_backup_root(server: Server) -> Path:
    """Per-server backup directory: ``BACKUP_DIR/<safe_name>/``."""
    return Path(BACKUP_DIR) / _safe_name(server.name)


def _dir_size(path: Path) -> int:
    """Apparent (logical) size of a directory tree, ignoring symlinks."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                if os.path.islink(fpath):
                    continue
                total += os.path.getsize(fpath)
            except OSError:
                continue
    return total


def list_backups(db: Session, server: Server) -> List[Backup]:
    """All backups for a server, newest first (zip and rsync)."""
    return (
        db.query(Backup)
        .filter(Backup.server_id == server.id)
        .order_by(Backup.created_at.desc())
        .all()
    )


def _last_successful_snapshot(db: Session, server: Server) -> Optional[Backup]:
    """Most recent successful rsync snapshot (any type) still present on disk.

    Used both as the rsync ``--link-dest`` source and for the backup-on-start /
    daily-schedule interval checks.
    """
    rows = (
        db.query(Backup)
        .filter(
            Backup.server_id == server.id,
            Backup.method == BACKUP_METHOD_RSYNC,
            Backup.status == BACKUP_OK,
        )
        .order_by(Backup.created_at.desc())
        .all()
    )
    for b in rows:
        if b.backup_path and os.path.isdir(b.backup_path):
            return b
    return None


def _perform_snapshot(
    db: Session, server: Server, backup_type: str, note: Optional[str] = None
) -> Backup:
    """Create one rsync snapshot generation. Caller must ensure the server is
    stopped. Records a ``Backup`` row (in_progress -> ok) and returns it; on
    failure the row is marked ``failed`` and the exception propagates.
    """
    if not shutil.which("rsync"):
        raise RuntimeError("rsync is not installed (required for v0.6 backups)")

    src_dir = Path(server.server_path)
    if not src_dir.is_dir():
        raise FileNotFoundError(f"Server path does not exist: {server.server_path}")

    server_root = _server_backup_root(server)
    server_root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime(BACKUP_TIMESTAMP_FMT)
    dest_dir = server_root / timestamp
    suffix = 1
    while dest_dir.exists():
        dest_dir = server_root / f"{timestamp}_{suffix}"
        suffix += 1

    if not _within(Path(BACKUP_DIR), dest_dir):
        raise RuntimeError("Refusing to write a backup outside BACKUP_DIR.")

    backup = Backup(
        server_id=server.id,
        backup_name=dest_dir.name,
        backup_path=str(dest_dir),
        size_bytes=0,
        method=BACKUP_METHOD_RSYNC,
        backup_type=backup_type,
        status=BACKUP_IN_PROGRESS,
        note=note,
    )
    db.add(backup)
    db.commit()
    db.refresh(backup)

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        cmd = ["rsync", "-a", "--delete"]
        link_dest = _last_successful_snapshot(db, server)
        if link_dest is not None:
            # Unchanged files become hardlinks into the previous generation, so
            # each generation is a full browsable tree at near-zero extra cost.
            cmd += ["--link-dest", str(Path(link_dest.backup_path).resolve())]
        src = str(src_dir.resolve()).rstrip(os.sep) + os.sep
        dst = str(dest_dir.resolve()).rstrip(os.sep) + os.sep
        cmd += [src, dst]

        subprocess.run(
            cmd, check=True, capture_output=True, text=True, timeout=RSYNC_TIMEOUT_SECONDS
        )

        backup.size_bytes = _dir_size(dest_dir)
        backup.status = BACKUP_OK
        db.commit()
        db.refresh(backup)
        return backup
    except Exception as exc:
        detail = str(exc)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            lines = exc.stderr.strip().splitlines()
            if lines:
                detail = lines[-1]
        backup.status = BACKUP_FAILED
        backup.note = ((note + " | ") if note else "") + f"error: {detail}"[:1000]
        db.commit()
        raise


def _prune_generations(db: Session, server: Server) -> None:
    """Keep only the newest ``backup_keep_generations`` successful *normal* rsync
    snapshots; delete older ones (directory + row). Pre-restore, zip, failed and
    in-progress backups are never pruned here.
    """
    keep = server.backup_keep_generations or 0
    if keep <= 0:
        return
    normals = (
        db.query(Backup)
        .filter(
            Backup.server_id == server.id,
            Backup.method == BACKUP_METHOD_RSYNC,
            Backup.backup_type == BACKUP_TYPE_NORMAL,
            Backup.status == BACKUP_OK,
        )
        .order_by(Backup.created_at.desc())
        .all()
    )
    for old in normals[keep:]:
        _delete_snapshot_files(old.backup_path)
        _append_managed_log(
            server, f"Backup retention: pruned old generation {old.backup_name}."
        )
        db.delete(old)
    db.commit()


def _delete_snapshot_files(path: Optional[str]) -> None:
    """Remove a snapshot directory. Strictly limited to paths under BACKUP_DIR,
    so registered server files can never be deleted here.
    """
    if not path:
        return
    p = Path(path)
    if not _within(Path(BACKUP_DIR), p):
        return
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)


def backup_now(
    db: Session, server: Server, backup_type: str = BACKUP_TYPE_NORMAL
) -> BackupOutcome:
    """Stop-aware rsync backup.

    Records the current run state, stops a running server (safe stop — RCON /
    stdin / SIGTERM, never SIGKILL), confirms it stopped, snapshots, prunes old
    generations, then restarts the server **only** if it had been running.
    Aborts (starting nothing) if a running server cannot be stopped.
    """
    from app.services import server_process

    server_process.refresh_status(db, server)
    was_running = server.status == STATUS_RUNNING

    if was_running:
        _append_managed_log(
            server, "Backup: stopping running server for a consistent snapshot."
        )
        server_process.stop_server(db, server)
        server_process.refresh_status(db, server)
        if server.status != STATUS_STOPPED:
            msg = "Backup ABORTED: server could not be stopped (no SIGKILL by policy)."
            _append_managed_log(server, msg)
            return BackupOutcome(ok=False, message=msg, aborted=True)

    outcome: BackupOutcome
    try:
        backup = _perform_snapshot(db, server, backup_type)
        _append_managed_log(
            server,
            f"Backup OK ({backup_type}): {backup.backup_name} "
            f"[{_fmt_size(backup.size_bytes)}].",
        )
        if backup_type == BACKUP_TYPE_NORMAL:
            _prune_generations(db, server)
        outcome = BackupOutcome(
            ok=True,
            backup=backup,
            message=(
                f"Backup created: {backup.backup_name} "
                f"({_fmt_size(backup.size_bytes)})."
            ),
        )
    except Exception as exc:
        msg = f"Backup FAILED: {exc}"
        _append_managed_log(server, msg)
        outcome = BackupOutcome(ok=False, message=msg)
    finally:
        if was_running:
            # We stopped a running server, so restore its running state whether
            # or not the snapshot itself succeeded.
            _append_managed_log(
                server, "Backup: restarting server (it was running before backup)."
            )
            try:
                server_process.start_server(db, server)
            except Exception as exc:  # never mask the backup outcome
                _append_managed_log(server, f"Backup: restart failed: {exc}")

    return outcome


def maybe_backup_on_start(db: Session, server: Server) -> StartGate:
    """Backup-on-start hook, called by the manual Start action *before* launch.

    - Disabled / already running -> allow start, no backup.
    - Within the min interval since the last successful snapshot -> skip (logged),
      allow start.
    - Otherwise take a backup; if it fails, abort the start.
    """
    from app.services import server_process

    if not server.backup_on_start_enabled:
        return StartGate(allow_start=True)

    server_process.refresh_status(db, server)
    if server.status == STATUS_RUNNING:
        # Nothing to snapshot consistently for a start of an already-running
        # server; let start_server() no-op.
        return StartGate(allow_start=True)

    interval_hours = server.backup_on_start_min_interval_hours or 0
    last = _last_successful_snapshot(db, server)
    if last is not None and interval_hours > 0:
        age = datetime.utcnow() - last.created_at
        if age < timedelta(hours=interval_hours):
            hours = age.total_seconds() / 3600.0
            _append_managed_log(
                server,
                f"Backup-on-start skipped: last backup {hours:.1f}h ago "
                f"(< {interval_hours}h min interval).",
            )
            return StartGate(allow_start=True)

    _append_managed_log(server, "Backup-on-start: taking a backup before launch.")
    outcome = backup_now(db, server, BACKUP_TYPE_NORMAL)
    if outcome.ok:
        return StartGate(allow_start=True, message="Backup-on-start completed; starting.")
    return StartGate(allow_start=False, message="backup failed, start aborted", level="error")


def delete_backup(db: Session, server: Server, backup: Backup) -> bool:
    """Delete a single backup (snapshot dir or zip file + DB row).

    Only ever removes files under BACKUP_DIR; registered server files are never
    touched.
    """
    if backup.server_id != server.id:
        return False
    if backup.backup_path:
        p = Path(backup.backup_path)
        if _within(Path(BACKUP_DIR), p):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
    db.delete(backup)
    db.commit()
    _append_managed_log(
        server, f"Backup deleted: {backup.backup_name} ({backup.backup_type})."
    )
    return True


def restore_backup(db: Session, server: Server, backup: Backup) -> RestoreOutcome:
    """Restore the server directory from an rsync snapshot.

    Only permitted while the server is **stopped**. A pre-restore safety snapshot
    is always taken first. The server is **not** auto-started afterwards.
    """
    from app.services import server_process

    if backup.server_id != server.id:
        return RestoreOutcome(False, "Backup does not belong to this server.", "error")

    server_process.refresh_status(db, server)
    if server.status != STATUS_STOPPED:
        msg = "Restore refused: server is running. Stop it first."
        _append_managed_log(server, msg)
        return RestoreOutcome(False, msg, "error")

    if (
        backup.method != BACKUP_METHOD_RSYNC
        or backup.status != BACKUP_OK
        or not backup.backup_path
        or not os.path.isdir(backup.backup_path)
    ):
        msg = "Restore failed: only completed rsync snapshots can be restored."
        _append_managed_log(server, msg)
        return RestoreOutcome(False, msg, "error")

    if not _within(Path(BACKUP_DIR), Path(backup.backup_path)):
        return RestoreOutcome(False, "Refusing to restore from outside BACKUP_DIR.", "error")

    # 1) Mandatory pre-restore safety snapshot (server is already stopped).
    try:
        pre = _perform_snapshot(
            db,
            server,
            BACKUP_TYPE_PRE_RESTORE,
            note=f"pre-restore safety snapshot before restoring {backup.backup_name}",
        )
        _append_managed_log(server, f"Pre-restore snapshot created: {pre.backup_name}.")
    except Exception as exc:
        msg = f"Restore ABORTED: pre-restore backup failed: {exc}"
        _append_managed_log(server, msg)
        return RestoreOutcome(False, msg, "error")

    # 2) Restore snapshot -> server_path (rsync --delete makes it a true revert).
    if not shutil.which("rsync"):
        return RestoreOutcome(False, "rsync is not installed.", "error")
    try:
        src = str(Path(backup.backup_path).resolve()).rstrip(os.sep) + os.sep
        dst = str(Path(server.server_path).resolve()).rstrip(os.sep) + os.sep
        subprocess.run(
            ["rsync", "-a", "--delete", src, dst],
            check=True,
            capture_output=True,
            text=True,
            timeout=RSYNC_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        detail = str(exc)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            lines = exc.stderr.strip().splitlines()
            if lines:
                detail = lines[-1]
        msg = f"Restore FAILED: {detail}"
        _append_managed_log(server, msg)
        return RestoreOutcome(False, msg, "error")

    server.status = STATUS_STOPPED
    server.pid = None
    db.commit()
    msg = (
        f"Restored from {backup.backup_name}. Pre-restore snapshot "
        f"{pre.backup_name} was created. Server left stopped — start it manually "
        f"when ready."
    )
    _append_managed_log(server, msg)
    return RestoreOutcome(True, msg, "info")


def run_due_server_backup(db: Session, server: Server, now: datetime) -> Optional[BackupOutcome]:
    """Run a v0.6 daily snapshot for ``server`` if it is enabled and due.

    Called once per scheduler tick per enabled server. ``backup_time`` (HH:MM) is
    matched against the host-local wall clock; a successful normal snapshot within
    ``BACKUP_DAILY_DEDUP_HOURS`` suppresses a duplicate run in the same minute or
    just after a restart. Returns the outcome when it ran, else ``None``.
    """
    if not server.backup_enabled:
        return None
    if (server.backup_time or "").strip() != now.strftime("%H:%M"):
        return None
    last = _last_successful_snapshot(db, server)
    if last is not None:
        if (datetime.utcnow() - last.created_at) < timedelta(hours=BACKUP_DAILY_DEDUP_HOURS):
            return None
    _append_managed_log(server, f"Scheduled daily backup triggered ({server.backup_time}).")
    return backup_now(db, server, BACKUP_TYPE_NORMAL)
