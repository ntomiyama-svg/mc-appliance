#!/usr/bin/env bash
#
# Rocky Linux 9 development-preparation script for mc-appliance.
#
# This is NOT a production installer. It only installs the OS packages needed
# to develop and run the v0.1 MVP. It makes no destructive changes: it does not
# create users, open firewall ports, configure systemd services, or touch any
# existing Minecraft data.
#
# Run with sudo (it only uses sudo for `dnf install`):
#   sudo bash scripts/install_rocky9.sh
set -euo pipefail

if ! command -v dnf >/dev/null 2>&1; then
  echo "ERROR: dnf not found. This script targets Rocky Linux 9 / RHEL 9." >&2
  exit 1
fi

echo "[install_rocky9] Installing development packages via dnf ..."

# Python 3.11 toolchain, git, zip utilities, and a JRE/JDK for running servers.
PACKAGES=(
  python3
  python3-pip
  python3-virtualenv
  git
  zip
  unzip
  # OpenJDK 17 is a common baseline for modern Minecraft servers. Adjust as
  # needed for your server version (e.g. java-21-openjdk).
  java-17-openjdk-headless
)

sudo dnf install -y "${PACKAGES[@]}"

echo
echo "[install_rocky9] Done. Next steps (as a NON-root user):"
echo "  cd to the project directory"
echo "  bash scripts/dev_run.sh"
echo
echo "NOTE: This script intentionally does not configure firewall, systemd, or"
echo "      HTTPS. Those belong to v0.2+. Do not expose the dev server to the"
echo "      public internet."
