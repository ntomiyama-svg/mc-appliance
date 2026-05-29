"""ORM models for servers and backups."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base

# Server status values used throughout the app.
STATUS_STOPPED = "stopped"
STATUS_RUNNING = "running"
STATUS_UNKNOWN = "unknown"
# Console "detected_state" values (v0.5). These describe what the live-log
# viewer *observes* (process + log markers) and are NOT persisted to the
# ``status`` column, which keeps its v0.1 running/stopped semantics so the
# existing start/stop/refresh logic and templates are unaffected.
STATUS_STARTING = "starting"
STATUS_STOPPING = "stopping"
STATUS_CRASHED = "crashed"
STATUS_FAILED_TO_START = "failed_to_start"


class Server(Base):
    __tablename__ = "servers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False, unique=True)
    server_path = Column(Text, nullable=False)
    jar_file = Column(String(255), nullable=True)
    java_path = Column(String(512), nullable=False, default="/usr/bin/java")
    min_memory = Column(String(16), nullable=False, default="1G")
    max_memory = Column(String(16), nullable=False, default="4G")
    start_command = Column(Text, nullable=True)
    pid = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default=STATUS_STOPPED)

    # Detection results.
    minecraft_type = Column(String(40), nullable=False, default="Unknown")
    minecraft_version = Column(String(40), nullable=True)
    has_mods = Column(Boolean, nullable=False, default=False)
    has_plugins = Column(Boolean, nullable=False, default=False)

    # RCON (v0.2). These mirror enable-rcon / rcon.port / rcon.password in the
    # server's server.properties so the app knows how to connect. The password
    # is never rendered in plaintext by the UI and never written to logs.
    rcon_enabled = Column(Boolean, nullable=False, default=False)
    rcon_host = Column(String(255), nullable=False, default="127.0.0.1")
    rcon_port = Column(Integer, nullable=False, default=25575)
    rcon_password = Column(String(255), nullable=True)
    rcon_last_status = Column(String(255), nullable=True)

    # Runtime-control bookkeeping (v0.5 "server runtime controls"). These record
    # the *outcome* of the most recent start/stop so the UI can show last start /
    # stop times, which stop method actually worked, the startup duration parsed
    # from the "Done (...)" line, and a short last-error summary. None of these
    # ever hold a secret — the RCON password is never written here.
    last_started_at = Column(DateTime, nullable=True)
    last_stopped_at = Column(DateTime, nullable=True)
    # "rcon" / "stdin" / "sigterm" / "sigkill" / "failed" / "already-stopped".
    last_stop_method = Column(String(32), nullable=True)
    last_stop_error = Column(String(255), nullable=True)
    last_start_error = Column(String(255), nullable=True)
    # Last detected console state (stopped/starting/running/stopping/crashed/
    # failed_to_start/unknown) — persisted so it survives without live polling.
    last_runtime_status = Column(String(32), nullable=True)
    last_startup_duration_seconds = Column(Integer, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    backups = relationship(
        "Backup",
        back_populates="server",
        cascade="all, delete-orphan",
        order_by="Backup.created_at.desc()",
    )


class Admin(Base):
    """A single administrator account (v0.1.2 single-admin auth).

    Passwords are never stored in plaintext — only the bcrypt hash produced by
    ``app.auth.hash_password`` lives here. Advanced multi-user / role
    management is intentionally out of scope until a later version.
    """

    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(120), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Backup(Base):
    __tablename__ = "backups"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("servers.id"), nullable=False, index=True)
    backup_name = Column(String(255), nullable=False)
    backup_path = Column(Text, nullable=False)
    size_bytes = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    server = relationship("Server", back_populates="backups")


# Backup-schedule types.
SCHEDULE_DAILY = "daily"
SCHEDULE_INTERVAL = "interval"


class BackupSchedule(Base):
    """A per-server automatic-backup schedule (v0.3).

    The in-process scheduler thread (``services/scheduler_service``) reads these
    rows and creates backups when one is due. The UI manages a single schedule
    per server, but the schema does not forbid more.

    NOTE on ``retention_count``: it is stored and displayed, but v0.3 does *not*
    prune old backups — file deletion is intentionally out of scope (see the
    v0.1 security model in CLAUDE.md and docs/12_backup_schedule.md).
    """

    __tablename__ = "backup_schedules"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("servers.id"), nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=False)
    # "daily" (run once a day at time_of_day) or "interval" (every N minutes).
    schedule_type = Column(String(20), nullable=False, default=SCHEDULE_DAILY)
    # "HH:MM" 24h local time; only meaningful when schedule_type == "daily".
    time_of_day = Column(String(5), nullable=True)
    # Minutes between runs; only meaningful when schedule_type == "interval".
    interval_minutes = Column(Integer, nullable=True)
    retention_count = Column(Integer, nullable=False, default=7)

    # Bookkeeping for the scheduler (so it can decide what is "due" and surface
    # the outcome in the UI). Not part of the minimal spec but harmless.
    last_run_at = Column(DateTime, nullable=True)
    last_status = Column(String(255), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    server = relationship("Server", backref="schedules")


# Jar-cache source / type constants.
JAR_SOURCE_MOJANG = "mojang"
JAR_TYPE_VANILLA = "Vanilla"


class ServerJarCache(Base):
    """A downloaded-and-cached server jar (v0.4).

    v0.4 only ever populates this with **Vanilla** jars fetched from Mojang's
    version manifest (``source='mojang'``); the schema is deliberately generic
    so Paper/Fabric/Forge/NeoForge sources can be added later without a
    migration. ``jar_path`` points at the cached file under ``JAR_CACHE_DIR``;
    the Create Server flow copies it to ``<server>/server.jar`` on demand.

    There is at most one row per (server_type, version) — re-downloading the
    same version refreshes the existing row rather than duplicating it.
    """

    __tablename__ = "server_jar_cache"
    __table_args__ = (
        UniqueConstraint("server_type", "version", name="uq_jar_type_version"),
    )

    id = Column(Integer, primary_key=True, index=True)
    server_type = Column(String(40), nullable=False, default=JAR_TYPE_VANILLA)
    version = Column(String(64), nullable=False, index=True)
    # "release" / "snapshot" / "old_beta" ... as reported by the manifest.
    release_type = Column(String(20), nullable=True)
    source = Column(String(40), nullable=False, default=JAR_SOURCE_MOJANG)
    # Absolute path to the cached jar on disk (null until a successful download).
    jar_path = Column(Text, nullable=True)
    # The per-version metadata URL (from the manifest) and the resolved
    # server.jar download URL. Kept for provenance / re-download.
    manifest_url = Column(Text, nullable=True)
    download_url = Column(Text, nullable=True)
    sha1 = Column(String(64), nullable=True)
    size_bytes = Column(Integer, nullable=True)
    # True for the row matching Mojang's current latest.release.
    is_latest = Column(Boolean, nullable=False, default=False)
    downloaded_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )
