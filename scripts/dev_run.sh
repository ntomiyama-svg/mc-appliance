#!/usr/bin/env bash
#
# Development launcher for mc-appliance.
# - ensures a virtualenv exists
# - installs requirements
# - starts uvicorn with auto-reload
#
# Do NOT run this as root. The app refuses to start as root by design.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

VENV_DIR="${VENV_DIR:-.venv}"

if [[ "$(id -u)" -eq 0 ]]; then
  echo "ERROR: Do not run mc-appliance as root. Use a normal user account." >&2
  exit 1
fi

# 1. Ensure the virtualenv exists.
if [[ ! -d "$VENV_DIR" ]]; then
  echo "[dev_run] Creating virtualenv in $VENV_DIR ..."
  python3 -m venv "$VENV_DIR"
fi

# 2. Activate and install requirements.
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
echo "[dev_run] Installing requirements ..."
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

# 3. Launch uvicorn with reload.
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"
echo "[dev_run] Starting uvicorn on http://${HOST}:${PORT} (reload enabled) ..."
exec uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
