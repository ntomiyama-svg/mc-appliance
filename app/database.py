"""SQLAlchemy engine, session factory and Base declarative class."""
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import DATABASE_URL

# `check_same_thread=False` is required because uvicorn serves requests from a
# threadpool while SQLite connections are otherwise bound to one thread.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

Base = declarative_base()


# Columns added after the original v0.1 schema. ``create_all`` only ever
# creates *missing tables* — it never alters an existing one — so a database
# created by an earlier version is upgraded here with ``ALTER TABLE ... ADD
# COLUMN``. This is a deliberately minimal migration step; Alembic is not used
# yet (see docs/09_rcon.md). Each entry is (column_name, SQLite column DDL).
_ADDED_COLUMNS = {
    "servers": [
        ("rcon_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
        ("rcon_host", "VARCHAR(255) NOT NULL DEFAULT '127.0.0.1'"),
        ("rcon_port", "INTEGER NOT NULL DEFAULT 25575"),
        ("rcon_password", "VARCHAR(255)"),
        ("rcon_last_status", "VARCHAR(255)"),
        # v0.5 server-runtime-controls bookkeeping (all nullable — no default
        # needed, an old row simply has no recorded last start/stop yet).
        ("last_started_at", "DATETIME"),
        ("last_stopped_at", "DATETIME"),
        ("last_stop_method", "VARCHAR(32)"),
        ("last_stop_error", "VARCHAR(255)"),
        ("last_start_error", "VARCHAR(255)"),
        ("last_runtime_status", "VARCHAR(32)"),
        ("last_startup_duration_seconds", "INTEGER"),
        # v0.6 per-server backup settings.
        ("backup_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
        ("backup_time", "VARCHAR(5) NOT NULL DEFAULT '04:00'"),
        ("backup_on_start_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
        ("backup_on_start_min_interval_hours", "INTEGER NOT NULL DEFAULT 6"),
        ("backup_keep_generations", "INTEGER NOT NULL DEFAULT 7"),
    ],
    "backups": [
        # v0.6 backup metadata. Existing v0.3 rows are zip backups, so the
        # method default for *pre-existing* rows is set to 'zip' here; new rows
        # get their value from the ORM default ('rsync').
        ("method", "VARCHAR(16) NOT NULL DEFAULT 'zip'"),
        ("backup_type", "VARCHAR(32) NOT NULL DEFAULT 'normal'"),
        ("status", "VARCHAR(16) NOT NULL DEFAULT 'ok'"),
        ("note", "TEXT"),
    ],
}


def _run_simple_migrations() -> None:
    """Add any columns that are missing from an existing database.

    Safe to run on every startup: brand-new databases already have every column
    (create_all built them), so this becomes a no-op; only databases predating a
    column get the ``ALTER TABLE``.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in existing_tables:
                continue  # create_all will have created it with all columns.
            present = {col["name"] for col in inspector.get_columns(table)}
            for name, ddl in columns:
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def init_db() -> None:
    """Create all tables, then apply lightweight column migrations."""
    # Import models so they are registered on the metadata before create_all.
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _run_simple_migrations()


def get_db():
    """FastAPI dependency that yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
