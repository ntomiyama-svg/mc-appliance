"""Pydantic schemas for request validation and API responses."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ServerRegister(BaseModel):
    """Payload submitted from the registration form."""

    name: str = Field(..., min_length=1, max_length=120)
    server_path: str = Field(..., min_length=1)
    java_path: str = Field(default="/usr/bin/java")
    min_memory: str = Field(default="1G", max_length=16)
    max_memory: str = Field(default="4G", max_length=16)
    jar_file: Optional[str] = Field(default=None)


class ServerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    server_path: str
    jar_file: Optional[str]
    java_path: str
    min_memory: str
    max_memory: str
    start_command: Optional[str]
    pid: Optional[int]
    status: str
    minecraft_type: str
    minecraft_version: Optional[str]
    has_mods: bool
    has_plugins: bool
    created_at: datetime
    updated_at: datetime


class BackupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    server_id: int
    backup_name: str
    backup_path: str
    size_bytes: int
    created_at: datetime


class DetectionResult(BaseModel):
    """Result of scanning a candidate server directory."""

    jar_candidates: List[str] = []
    has_server_properties: bool = False
    has_eula: bool = False
    has_latest_log: bool = False
    has_world: bool = False
    has_mods: bool = False
    has_plugins: bool = False
    has_config: bool = False
    minecraft_type: str = "Unknown"
    minecraft_version: Optional[str] = None


class ActionResult(BaseModel):
    ok: bool
    message: str
