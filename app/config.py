"""Application-wide configuration and path constants.

For local development every path defaults to a location *inside the project
root*, so the app behaves exactly as it did in v0.1 with no extra setup.

For a system install (the v0.1.1 systemd service) the storage locations are
moved out of the code tree (``/var/lib`` / ``/var/log``). Those locations can be
overridden in two ways, highest precedence first:

    1. Environment variable   (MC_APPLIANCE_DATA_DIR, MC_APPLIANCE_LOG_DIR, ...)
    2. YAML config file        (/etc/mc-appliance/config.yaml, or $MC_APPLIANCE_CONFIG)
    3. Built-in default        (project-root relative — the dev layout)

Config file resolution (v0.3): the YAML file is located, highest precedence
first, by:

    1. ``$MC_APPLIANCE_CONFIG``                    (explicit override; the
       systemd unit always sets this to /etc/mc-appliance/config.yaml)
    2. ``<project>/data/config.yaml``              (dev only — so a checkout can
       keep its own config without touching /etc)
    3. ``/etc/mc-appliance/config.yaml``           (the system-install default)

PyYAML is an *optional* dependency: if it is not installed, or the config file
does not exist, we silently fall back to the defaults. This keeps a bare dev
checkout working without any new requirements. A config file that *exists* but
cannot be read (e.g. /etc/mc-appliance/config.yaml owned by root and not group
-readable while you are iterating as a normal user) degrades to a warning + the
built-in defaults in a dev environment, rather than aborting startup.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Project root = parent of the `app/` package directory.
BASE_DIR = Path(__file__).resolve().parent.parent

APP_NAME = "mc-appliance"
APP_VERSION = "0.3.0"

# Default location of the optional system config file.
DEFAULT_CONFIG_PATH = "/etc/mc-appliance/config.yaml"


def _is_dev_environment() -> bool:
    """Best-effort "are we a dev checkout, not a system install?" decision.

    ``MC_APPLIANCE_ENV`` wins when set (dev_run.sh exports ``dev``; an operator
    may set ``prod``). Otherwise we assume a system install lives under ``/opt``
    (where scripts/install.sh deploys it) and anything else is a dev checkout.
    """
    env = os.environ.get("MC_APPLIANCE_ENV", "").strip().lower()
    if env in ("dev", "development"):
        return True
    if env in ("prod", "production"):
        return False
    return not str(BASE_DIR).startswith("/opt/")


IS_DEV = _is_dev_environment()


def _config_candidate_paths() -> List[Path]:
    """Ordered list of candidate config-file locations (see module docstring)."""
    explicit = os.environ.get("MC_APPLIANCE_CONFIG")
    if explicit:
        return [Path(explicit)]
    candidates: List[Path] = []
    if IS_DEV:
        candidates.append(BASE_DIR / "data" / "config.yaml")
    candidates.append(Path(DEFAULT_CONFIG_PATH))
    return candidates


def _load_file_config() -> Dict[str, Any]:
    """Load the first usable YAML config file, returning {} if none usable.

    Never raises: a missing file, missing PyYAML, or malformed YAML all degrade
    to "use the defaults" so that a dev checkout (and the root-user guard) keep
    working. A file that exists but cannot be *read* emits a warning in a dev
    environment (and is otherwise skipped) instead of bringing the app down.
    """
    try:
        import yaml  # optional dependency
    except ImportError:
        return {}

    for path in _config_candidate_paths():
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except PermissionError:
            # Common in dev: /etc/mc-appliance/config.yaml is root:mcapp 0640 and
            # the developer's account is not in the mcapp group. Warn, don't die.
            if IS_DEV:
                print(
                    f"[config] WARNING: cannot read {path} (permission denied); "
                    "falling back to built-in defaults.",
                    file=sys.stderr,
                )
            continue
        except (OSError, yaml.YAMLError) as exc:
            if IS_DEV:
                print(
                    f"[config] WARNING: could not parse {path} ({exc}); "
                    "falling back to built-in defaults.",
                    file=sys.stderr,
                )
            continue
        if isinstance(data, dict):
            return data
    return {}


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

# Default parent directory for servers *created* through the GUI (v0.3). A
# system install points DATA_DIR at /var/lib/mc-appliance, so this becomes
# /var/lib/mc-appliance/servers; a dev checkout gets ./data/servers. Both can be
# overridden explicitly. Registered (pre-existing) servers are unaffected —
# their paths are wherever the operator pointed them.
SERVERS_DIR = _resolve_path(
    "MC_APPLIANCE_SERVERS_DIR", "servers_dir", DATA_DIR / "servers"
)

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

# --- Server creation (v0.3) ----------------------------------------------------
# Server "types" offered by the Create Server form. The chosen value is stored
# in Server.minecraft_type. "Custom" lets an operator upload any server jar.
SERVER_TYPES = ["Vanilla", "Paper", "Fabric", "Forge", "NeoForge", "Custom"]

# Allowed gamemode / difficulty values for the generated server.properties. Any
# other value is rejected by the form so we never write garbage into the file.
GAMEMODES = ["survival", "creative", "adventure", "spectator"]
DIFFICULTIES = ["peaceful", "easy", "normal", "hard"]

# Upload limits for the only file type the app accepts: Minecraft server /
# mod / plugin jars. ``.jar`` is the *only* permitted extension. The size cap
# guards against accidentally filling the disk via the browser upload field.
ALLOWED_UPLOAD_EXTENSIONS = (".jar",)
MAX_JAR_UPLOAD_BYTES = int(
    os.environ.get("MC_APPLIANCE_MAX_JAR_BYTES") or _FILE_CONFIG.get(
        "max_jar_upload_bytes", 500 * 1024 * 1024  # 500 MiB
    )
)

# --- Backup scheduler (v0.3) ---------------------------------------------------
# How often the in-process scheduler thread wakes to look for due backups. Kept
# coarse (a minute) because schedules are daily / interval-in-minutes.
SCHEDULER_TICK_SECONDS = 60
# Default retention shown in the schedule form. NOTE: v0.3 stores and displays
# retention but does NOT prune old backups — deletion stays out of scope per the
# v0.1 "no file deletion" rule (see docs/12_backup_schedule.md).
DEFAULT_RETENTION_COUNT = 7

# How long (seconds) to wait for a graceful SIGTERM shutdown before reporting
# the stop as failed. Neither v0.1 nor v0.2 escalate to SIGKILL automatically.
STOP_TIMEOUT_SECONDS = 10

# --- RCON (v0.2) ---------------------------------------------------------------
# Defaults applied to a newly registered server's RCON connection settings.
DEFAULT_RCON_HOST = "127.0.0.1"
DEFAULT_RCON_PORT = 25575
# Socket timeout (seconds) for a single RCON connect/command. Kept short so a
# misconfigured or unreachable server fails fast in the UI.
RCON_CONNECT_TIMEOUT_SECONDS = 5
# How long (seconds) to wait for the process to actually exit after issuing an
# RCON `stop` before falling back to the SIGTERM path.
RCON_STOP_TIMEOUT_SECONDS = 30

# Number of trailing lines of latest.log to show.
LOG_TAIL_LINES = 200

# Ensure required directories exist at import time.
for _d in (DATA_DIR, BACKUP_DIR, MANAGED_LOG_DIR, SERVERS_DIR):
    os.makedirs(_d, exist_ok=True)

DATABASE_URL = f"sqlite:///{DB_PATH}"

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
