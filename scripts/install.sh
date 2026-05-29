#!/usr/bin/env bash
#
# mc-appliance system installer (v0.1.1).
#
# Installs mc-appliance as a systemd-managed service running as a dedicated,
# UNPRIVILEGED user. It:
#   1. installs OS packages (dnf on Rocky/RHEL, apt on Debian/Ubuntu)
#   2. creates the system user 'mcapp'
#   3. deploys the app to /opt/mc-appliance
#   4. builds a virtualenv at /opt/mc-appliance/.venv and installs requirements
#   5. creates /var/lib/mc-appliance (DB/data) and /var/log/mc-appliance (logs)
#   6. writes a default /etc/mc-appliance/config.yaml (kept if it already exists)
#   7. installs and reloads the mc-appliance.service systemd unit
#
# Scope note (v0.1.1): this sets up only the mc-appliance web app as a service.
# It does NOT create systemd units for individual Minecraft servers, and does
# NOT configure auth/HTTPS/RCON/firewall. Those remain v0.2+ items.
#
# Run as root (it changes system state). The *web app itself* still never runs
# as root: it runs as 'mcapp', and the app aborts startup if euid == 0.
#
#   sudo bash scripts/install.sh
#
# Tunables (override via environment):
#   APP_USER=mcapp  INSTALL_DIR=/opt/mc-appliance  DATA_DIR=/var/lib/mc-appliance
#   LOG_DIR=/var/log/mc-appliance  CONFIG_DIR=/etc/mc-appliance
#   SKIP_PKG=1      skip OS-package installation (you installed them yourself)
#   ENABLE_SERVICE=1  also `systemctl enable --now` the service at the end
#   ENABLE_JAVA_INSTALLER=1  deploy the Java installer helper and write the
#                   minimal /etc/sudoers.d rule so the GUI "Install Java" buttons
#                   work. OFF by default — the GUI installer stays disabled until
#                   an operator opts in. See docs/16_java_installer.md.
set -euo pipefail

APP_USER="${APP_USER:-mcapp}"
APP_GROUP="${APP_GROUP:-$APP_USER}"
INSTALL_DIR="${INSTALL_DIR:-/opt/mc-appliance}"
DATA_DIR="${DATA_DIR:-/var/lib/mc-appliance}"
LOG_DIR="${LOG_DIR:-/var/log/mc-appliance}"
CONFIG_DIR="${CONFIG_DIR:-/etc/mc-appliance}"
CONFIG_FILE="$CONFIG_DIR/config.yaml"
BACKUP_DIR="${BACKUP_DIR:-$DATA_DIR/backups}"
VENV_DIR="$INSTALL_DIR/.venv"
SERVICE_NAME="mc-appliance.service"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

log() { echo "[install] $*"; }
err() { echo "[install] ERROR: $*" >&2; }

# --- 0. Preconditions ---------------------------------------------------------
if [[ "$(id -u)" -ne 0 ]]; then
  err "This installer must run as root (creates a user, writes /opt /etc /var,"
  err "installs a systemd unit). Re-run:  sudo bash scripts/install.sh"
  exit 1
fi

# --- 1. OS packages -----------------------------------------------------------
if [[ "${SKIP_PKG:-0}" == "1" ]]; then
  log "SKIP_PKG=1 set; skipping OS-package installation."
elif command -v dnf >/dev/null 2>&1; then
  log "Detected dnf (Rocky/RHEL family); delegating to install_rocky9.sh ..."
  bash "$SCRIPT_DIR/install_rocky9.sh"
elif command -v apt-get >/dev/null 2>&1; then
  log "Detected apt (Debian/Ubuntu family); installing packages ..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y \
    python3 python3-venv python3-pip git tar zip unzip openjdk-17-jre-headless
else
  err "No supported package manager (dnf/apt) found."
  err "Install python3 + venv + pip, git, tar, zip, unzip and a JRE manually,"
  err "then re-run with SKIP_PKG=1."
  exit 1
fi

# --- 2. System user -----------------------------------------------------------
if getent passwd "$APP_USER" >/dev/null 2>&1; then
  log "User '$APP_USER' already exists; reusing it."
else
  log "Creating system user '$APP_USER' ..."
  NOLOGIN="$(command -v nologin || echo /sbin/nologin)"
  useradd --system --user-group \
    --home-dir "$INSTALL_DIR" --shell "$NOLOGIN" \
    --comment "mc-appliance service account" "$APP_USER"
fi

# --- 3. Deploy application to INSTALL_DIR -------------------------------------
mkdir -p "$INSTALL_DIR"
if [[ "$PROJECT_ROOT" == "$INSTALL_DIR" ]]; then
  log "Running from $INSTALL_DIR already; skipping file copy."
else
  log "Deploying application to $INSTALL_DIR ..."
  # tar pipe copies just the app payload, excluding the venv, runtime data,
  # git metadata and caches. tar is always present (installed above).
  tar -C "$PROJECT_ROOT" \
    --exclude='./.venv' --exclude='./.git' --exclude='./data' \
    --exclude='./backups' --exclude='*/__pycache__' --exclude='*.pyc' \
    -cf - app scripts requirements.txt README.md CLAUDE.md docs \
    | tar -C "$INSTALL_DIR" -xf -
fi

# --- 4. Virtualenv + dependencies --------------------------------------------
log "Creating Python virtualenv at $VENV_DIR ..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip >/dev/null
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# --- 5. State / log directories ----------------------------------------------
log "Creating state and log directories ..."
# DATA_DIR/servers holds servers *created* through the GUI (v0.3); it is created
# here so it has the right ownership up front (chown -R DATA_DIR below covers it).
mkdir -p "$DATA_DIR" "$DATA_DIR/servers" "$LOG_DIR" "$BACKUP_DIR" "$CONFIG_DIR"

# --- 6. Config file (never clobber an existing one) ---------------------------
if [[ -f "$CONFIG_FILE" ]]; then
  log "Config $CONFIG_FILE already exists; leaving it untouched."
else
  log "Writing default config to $CONFIG_FILE ..."
  cat > "$CONFIG_FILE" <<EOF
# mc-appliance configuration (v0.1.1)
#
# Storage locations for the service. Equivalent MC_APPLIANCE_DATA_DIR /
# MC_APPLIANCE_LOG_DIR / MC_APPLIANCE_BACKUP_DIR environment variables, if set,
# take precedence over these values.
data_dir: $DATA_DIR
log_dir: $LOG_DIR
backup_dir: $BACKUP_DIR
# Parent directory for servers created via the GUI (v0.3). Defaults to
# <data_dir>/servers if omitted.
servers_dir: $DATA_DIR/servers

# Web UI bind address/port (informational: the systemd unit passes --host/--port
# to uvicorn explicitly). v0.1.x has NO authentication or HTTPS — keep this on a
# trusted LAN only.
host: 0.0.0.0
port: 8080
EOF
fi

# --- 7. Ownership / permissions ----------------------------------------------
log "Setting ownership and permissions ..."
# App code is owned by root, readable/executable by the service group, so the
# web app cannot rewrite its own code at runtime.
chown -R "root:$APP_GROUP" "$INSTALL_DIR"
chmod -R g+rX "$INSTALL_DIR"
# Persistent state and logs are writable by the service user.
chown -R "$APP_USER:$APP_GROUP" "$DATA_DIR" "$LOG_DIR"
# Config: root-owned, readable (not writable) by the service group.
chown "root:$APP_GROUP" "$CONFIG_DIR" "$CONFIG_FILE"
chmod 750 "$CONFIG_DIR"
chmod 640 "$CONFIG_FILE"

# --- 8. systemd unit ----------------------------------------------------------
log "Installing systemd unit $SERVICE_NAME ..."
install -m 0644 "$SCRIPT_DIR/mc-appliance.service" "/etc/systemd/system/$SERVICE_NAME"
systemctl daemon-reload

if [[ "${ENABLE_SERVICE:-0}" == "1" ]]; then
  log "Enabling and starting $SERVICE_NAME ..."
  systemctl enable --now "$SERVICE_NAME"
fi

# --- 9. (opt-in) Java installer helper + sudoers ------------------------------
# By default the GUI "Install Java" feature is DISABLED: we deploy nothing here
# and the web app's sudoers probe finds no rule, so the UI shows "GUI install is
# not enabled". Set ENABLE_JAVA_INSTALLER=1 to opt in. We then:
#   * install the helper at a fixed, root-owned path (NOT under the app tree, so
#     a compromised app cannot rewrite the very script it can run as root), and
#   * write a MINIMAL sudoers rule: NOPASSWD for that exact script with exactly
#     one of the five allowed major versions as its only argument. No wildcards.
JAVA_HELPER_DIR="/usr/local/lib/mc-appliance"
JAVA_HELPER="$JAVA_HELPER_DIR/install_java_runtime.sh"
JAVA_SUDOERS="/etc/sudoers.d/mc-appliance-java"

if [[ "${ENABLE_JAVA_INSTALLER:-0}" == "1" ]]; then
  log "ENABLE_JAVA_INSTALLER=1: deploying Java installer helper ..."
  mkdir -p "$JAVA_HELPER_DIR"
  install -m 0755 -o root -g root \
    "$SCRIPT_DIR/install_java_runtime.sh" "$JAVA_HELPER"

  log "Writing sudoers rule $JAVA_SUDOERS (minimal, per-version NOPASSWD) ..."
  tmp_sudoers="$(mktemp)"
  {
    echo "# Managed by mc-appliance install.sh (ENABLE_JAVA_INSTALLER=1)."
    echo "# Allows the unprivileged service account to install ONLY the five"
    echo "# supported Java major versions via the dedicated helper. No wildcards,"
    echo "# no free-form commands, no arbitrary package names."
    for v in 8 11 17 21 25; do
      echo "$APP_USER ALL=(root) NOPASSWD: $JAVA_HELPER $v"
    done
  } >"$tmp_sudoers"

  # Validate before installing — a bad sudoers file can lock out sudo entirely.
  if visudo -c -f "$tmp_sudoers" >/dev/null; then
    install -m 0440 -o root -g root "$tmp_sudoers" "$JAVA_SUDOERS"
    rm -f "$tmp_sudoers"
    log "Java GUI installer enabled for user '$APP_USER'."
  else
    rm -f "$tmp_sudoers"
    err "Generated sudoers file failed validation; NOT installing it."
    err "The GUI Java installer remains disabled."
  fi
else
  log "ENABLE_JAVA_INSTALLER not set; GUI Java installer stays DISABLED (safe default)."
fi

# --- Done ---------------------------------------------------------------------
cat <<EOF

[install] Done.

  App dir:    $INSTALL_DIR   (owned root:$APP_GROUP, read-only to the service)
  Venv:       $VENV_DIR
  Data/DB:    $DATA_DIR      (owned $APP_USER)
  Logs:       $LOG_DIR       (owned $APP_USER)
  Config:     $CONFIG_FILE
  Service:    $SERVICE_NAME  (User=$APP_USER)

Next steps:
  sudo systemctl enable --now $SERVICE_NAME    # start now + on boot
  systemctl status $SERVICE_NAME
  journalctl -u $SERVICE_NAME -f               # follow logs

Then open  http://<server-ip>:8080/

Java Runtime Manager:
EOF
if [[ "${ENABLE_JAVA_INSTALLER:-0}" == "1" ]]; then
  echo "  GUI Java installer is ENABLED (sudoers: $JAVA_SUDOERS)."
else
  echo "  GUI Java installer is DISABLED (default). Re-run with"
  echo "  ENABLE_JAVA_INSTALLER=1 to allow installing Java from the GUI."
fi
cat <<EOF

Reminder: keep mc-appliance on a trusted LAN.
EOF
