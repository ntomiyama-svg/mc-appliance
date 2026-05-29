"""In-process backup scheduler (v0.3).

A single daemon thread wakes every ``SCHEDULER_TICK_SECONDS`` and creates a
backup for every enabled :class:`~app.models.BackupSchedule` that is due. We use
a plain thread rather than APScheduler so v0.3 adds **no new dependency**; the
trade-off (no second-level precision, schedules only run while the app is up) is
fine for daily/interval-in-minutes backups.

Design goals:

* **Never take the app down.** Every layer is wrapped in try/except and the
  thread is a daemon; a failure logs and the loop keeps going.
* **Pick up schedule changes automatically.** The DB is re-read every tick, so
  edits made in the UI take effect on the next pass — there is nothing to
  "reload".
* **Leave an audit trail.** Outcomes go to ``<LOG_DIR>/backup_scheduler.log``
  and to ``BackupSchedule.last_run_at`` / ``last_status``.

NOTE: ``retention_count`` is honoured only as *metadata* in v0.3. Pruning old
backups would mean deleting files, which is intentionally out of scope (see the
v0.1 "no file deletion" rule). We log the current count instead.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from app.config import LOG_DIR, SCHEDULER_TICK_SECONDS
from app.database import SessionLocal
from app.models import (
    SCHEDULE_DAILY,
    SCHEDULE_INTERVAL,
    STATUS_RUNNING,
    BackupSchedule,
    Server,
)

logger = logging.getLogger("mc_appliance.scheduler")


def _configure_logger() -> None:
    """Attach a file handler under LOG_DIR (best-effort; falls back to stderr)."""
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    try:
        handler: logging.Handler = logging.FileHandler(
            str(LOG_DIR / "backup_scheduler.log"), encoding="utf-8"
        )
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(fmt)
    logger.addHandler(handler)


_thread: Optional[threading.Thread] = None
_started = False
_lock = threading.Lock()


def _parse_hhmm(value: Optional[str]) -> Optional[tuple]:
    """Parse ``"HH:MM"`` into ``(hour, minute)`` or ``None`` if malformed."""
    if not value:
        return None
    try:
        hh_str, mm_str = value.split(":", 1)
        hh, mm = int(hh_str), int(mm_str)
    except (ValueError, AttributeError):
        return None
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        return hh, mm
    return None


def _is_due(schedule: BackupSchedule, now: datetime) -> bool:
    """Return whether ``schedule`` should run at ``now``."""
    if not schedule.enabled:
        return False

    if schedule.schedule_type == SCHEDULE_INTERVAL:
        minutes = schedule.interval_minutes or 0
        if minutes <= 0:
            return False
        if schedule.last_run_at is None:
            return True
        return (now - schedule.last_run_at) >= timedelta(minutes=minutes)

    if schedule.schedule_type == SCHEDULE_DAILY:
        hhmm = _parse_hhmm(schedule.time_of_day)
        if hhmm is None:
            return False
        scheduled_today = now.replace(
            hour=hhmm[0], minute=hhmm[1], second=0, microsecond=0
        )
        if now < scheduled_today:
            return False
        # Run only once per scheduled instant: skip if we already ran at/after it.
        return schedule.last_run_at is None or schedule.last_run_at < scheduled_today

    return False


def _run_due_backup(db, schedule: BackupSchedule, now: datetime) -> None:
    """Create one backup for a due schedule and record the outcome.

    Imported lazily to avoid an import cycle at module load and to keep the
    optional RCON path isolated.
    """
    from app.services import backup_service, rcon_service, server_process

    server = db.get(Server, schedule.server_id)
    if server is None:
        schedule.last_run_at = now
        schedule.last_status = "Skipped: server no longer exists."
        db.commit()
        logger.warning("schedule %s: server %s missing", schedule.id, schedule.server_id)
        return

    # Best-effort consistent snapshot: flush via RCON if the server is up.
    warn = ""
    try:
        server_process.refresh_status(db, server)
        if server.rcon_enabled and server.status == STATUS_RUNNING:
            save_res = rcon_service.save_all(server, flush=True)
            if not save_res["ok"]:
                warn = f"(RCON save-all failed: {save_res['message']}) "
    except Exception as exc:  # noqa: BLE001 - never let a save attempt abort the backup
        warn = f"(pre-backup save error: {exc}) "

    _, msg = backup_service.create_backup(
        db, server, exclude_logs=True, exclude_backups=True
    )

    # Retention is metadata only in v0.3 — log the count, never delete.
    kept = len(server.backups)
    retention_note = (
        f" Retention={schedule.retention_count}; {kept} backups kept "
        "(pruning deferred per no-delete policy)."
    )

    schedule.last_run_at = now
    schedule.last_status = (warn + msg)[:250]
    db.commit()
    logger.info("schedule %s (%s): %s%s%s", schedule.id, server.name, warn, msg, retention_note)


def _tick() -> None:
    """One scheduler pass. Each schedule is isolated so one failure can't stop others."""
    now = datetime.now()
    db = SessionLocal()
    try:
        schedules = db.query(BackupSchedule).filter(BackupSchedule.enabled.is_(True)).all()
        for schedule in schedules:
            try:
                if _is_due(schedule, now):
                    _run_due_backup(db, schedule, now)
            except Exception as exc:  # noqa: BLE001 - log and continue with the next one
                logger.exception("schedule %s failed: %s", schedule.id, exc)
                try:
                    db.rollback()
                    schedule.last_run_at = now
                    schedule.last_status = f"Failed: {exc}"[:250]
                    db.commit()
                except Exception:  # noqa: BLE001
                    db.rollback()
    finally:
        db.close()


def _loop() -> None:
    logger.info("backup scheduler started (tick=%ss)", SCHEDULER_TICK_SECONDS)
    while True:
        try:
            _tick()
        except Exception as exc:  # noqa: BLE001 - the loop must never die
            logger.exception("scheduler tick crashed: %s", exc)
        time.sleep(SCHEDULER_TICK_SECONDS)


def start_scheduler() -> None:
    """Start the daemon thread once. Safe to call from the FastAPI startup hook.

    Any failure here is swallowed: a broken scheduler must not stop the web app
    from serving.
    """
    global _thread, _started
    with _lock:
        if _started:
            return
        _started = True
        try:
            _configure_logger()
            count = _enabled_count()
            logger.info("loading schedules at startup: %s enabled", count)
            _thread = threading.Thread(
                target=_loop, name="mc-backup-scheduler", daemon=True
            )
            _thread.start()
        except Exception as exc:  # noqa: BLE001
            logger.exception("could not start backup scheduler: %s", exc)


def _enabled_count() -> int:
    db = SessionLocal()
    try:
        return db.query(BackupSchedule).filter(BackupSchedule.enabled.is_(True)).count()
    except Exception:  # noqa: BLE001 - DB may not be ready; treat as zero
        return 0
    finally:
        db.close()


def enabled_schedule_count() -> int:
    """Public helper for the dashboard: number of enabled schedules."""
    return _enabled_count()
