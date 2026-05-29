"""Application-wide configuration and path constants.

All paths are resolved relative to the project root so the app works the same
whether launched from the repo directory or a systemd unit later on.
"""
from __future__ import annotations

import os
from pathlib import Path

# Project root = parent of the `app/` package directory.
BASE_DIR = Path(__file__).resolve().parent.parent

# Data / storage locations.
DATA_DIR = BASE_DIR / "data"
BACKUP_DIR = BASE_DIR / "backups"
DB_PATH = DATA_DIR / "mc_appliance.db"

# Managed-log directory: stdout/stderr of servers we launch is captured here
# (in addition to the server's own logs/latest.log).
MANAGED_LOG_DIR = DATA_DIR / "server_logs"

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

APP_NAME = "mc-appliance"
APP_VERSION = "0.1.2"

# Secret key used to sign session cookies (Starlette SessionMiddleware).
#
# SECURITY: the fallback below is for DEVELOPMENT ONLY. Anyone who knows it can
# forge admin sessions. In any real deployment, set a long random value via the
# MC_APPLIANCE_SECRET_KEY environment variable, e.g.:
#
#     export MC_APPLIANCE_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
#
# See README "Security notes" and docs/08_authentication.md.
SECRET_KEY = os.environ.get(
    "MC_APPLIANCE_SECRET_KEY",
    "dev-insecure-change-me-please-set-MC_APPLIANCE_SECRET_KEY",
)
