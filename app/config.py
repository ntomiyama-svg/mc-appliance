"""Application-wide configuration and path constants.

For local development every path defaults to a location *inside the project
root*, so the app behaves exactly as it did in v0.1 with no extra setup.

For a system install (the v0.1.1 systemd service) the storage locations are
moved out of the code tree (``/var/lib`` / ``/var/log``). Those locations can be
overridden in two ways, highest precedence first:

    1. Environment variable   (MC_APPLIANCE_DATA_DIR, MC_APPLIANCE_LOG_DIR, ...)
    2. YAML config file        (/etc/mc-appliance/config.yaml, or $MC_APPLIANCE_CONFIG)
    3. Built-in default        (project-root relative — the dev layout)

PyYAML is an *optional* dependency: if it is not installed, or the config file
does not exist, we silently fall back to the defaults. This keeps a bare dev
checkout working without any new requirements.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

# Project root = parent of the `app/` package directory.
BASE_DIR = Path(__file__).resolve().parent.parent

APP_NAME = "mc-appliance"
APP_VERSION = "0.1.1"

# Default location of the optional system config file.
DEFAULT_CONFIG_PATH = "/etc/mc-appliance/config.yaml"


def _load_file_config() -> Dict[str, Any]:
    """Load the optional YAML config file, returning {} if unavailable.

    Never raises: a missing file, missing PyYAML, or malformed YAML all degrade
    to "use the defaults" so that a dev checkout (and the root-user guard) keep
    working.
    """
    cfg_path = os.environ.get("MC_APPLIANCE_CONFIG", DEFAULT_CONFIG_PATH)
    path = Path(cfg_path)
    if not path.is_file():
        return {}
    try:
        import yaml  # optional dependency
    except ImportError:
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


_FILE_CONFIG = _load_file_config()


def _resolve_path(env_key: str, file_key: str, default: Path) -> Path:
    """Resolve a directory from env var, then config file, then default."""
    value: Optional[str] = os.environ.get(env_key)
    if not value:
        raw = _FILE_CONFIG.get(file_key)
        value = str(raw) if raw else None
    return Path(value).expanduser() if value else default


# --- Storage / data locations --------------------------------------------------
DATA_DIR = _resolve_path("MC_APPLIANCE_DATA_DIR", "data_dir", BASE_DIR / "data")
BACKUP_DIR = _resolve_path(
    "MC_APPLIANCE_BACKUP_DIR", "backup_dir", BASE_DIR / "backups"
)
# Log directory. Defaults to DATA_DIR so a dev checkout keeps managed server logs
# under data/server_logs (unchanged from v0.1); a system install points this at
# /var/log/mc-appliance via the config file.
LOG_DIR = _resolve_path("MC_APPLIANCE_LOG_DIR", "log_dir", DATA_DIR)

DB_PATH = DATA_DIR / "mc_appliance.db"

# Managed-log directory: stdout/stderr of servers we launch is captured here
# (in addition to the server's own logs/latest.log).
MANAGED_LOG_DIR = LOG_DIR / "server_logs"

# Network bind for the web UI. Informational in v0.1.1 (dev_run.sh and the
# systemd unit both pass --host/--port to uvicorn explicitly), but exposed here
# so config.yaml stays the single source of truth.
HOST = os.environ.get("MC_APPLIANCE_HOST") or str(_FILE_CONFIG.get("host", "0.0.0.0"))
PORT = int(os.environ.get("MC_APPLIANCE_PORT") or _FILE_CONFIG.get("port", 8080))

# Defaults used by the registration form.
DEFAULT_JAVA_PATH = "/usr/bin/java"
DEFAULT_MIN_MEMORY = "1G"
DEFAULT_MAX_MEMORY = "4G"

# How long (seconds) to wait for a graceful SIGTERM shutdown before reporting
# the stop as failed. v0.1 deliberately does NOT escalate to SIGKILL.
STOP_TIMEOUT_SECONDS = 10

# Number of trailing lines of latest.log to show.
LOG_TAIL_LINES = 200

# Ensure required directories exist at import time.
for _d in (DATA_DIR, BACKUP_DIR, MANAGED_LOG_DIR):
    os.makedirs(_d, exist_ok=True)

DATABASE_URL = f"sqlite:///{DB_PATH}"
