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
)
from sqlalchemy.orm import relationship

from app.database import Base

# Server status values used throughout the app.
STATUS_STOPPED = "stopped"
STATUS_RUNNING = "running"
STATUS_UNKNOWN = "unknown"


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
