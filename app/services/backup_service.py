"""Create zip backups of a registered server directory."""
from __future__ import annotations

import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Tuple

from sqlalchemy.orm import Session

from app.config import BACKUP_DIR
from app.models import Backup, Server

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
    )
    db.add(backup)
    db.commit()
    db.refresh(backup)

    return backup, f"Backup created: {zip_name} ({file_count} files, {size} bytes)."
