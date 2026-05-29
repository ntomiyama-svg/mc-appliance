#!/usr/bin/env bash
#
# Rocky Linux 9 / RHEL 9 OS-package installation for mc-appliance.
#
# This installs ONLY the OS packages mc-appliance needs (Python toolchain, git,
# zip utilities, a JRE for running servers). It makes no other changes: it does
# not create users, open firewall ports, configure systemd, or touch existing
# Minecraft data.
#
# Two ways to use it:
#   * Standalone (dev preparation):   sudo bash scripts/install_rocky9.sh
#   * Called by the full installer:   scripts/install.sh delegates here when it
#     detects dnf, so the package list lives in exactly one place.
#
# It works whether invoked as root (sudo not required) or as a normal user with
# sudo available.
set -euo pipefail

if ! command -v dnf >/dev/null 2>&1; then
  echo "ERROR: dnf not found. This script targets Rocky Linux 9 / RHEL 9." >&2
  exit 1
fi

# Use sudo only when we are not already root.
if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=""
elif command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
else
  echo "ERROR: need root privileges to install packages, but sudo is not available." >&2
  echo "       Re-run this script as root." >&2
  exit 1
fi

echo "[install_rocky9] Installing OS packages via dnf ..."

# Python toolchain, git, zip utilities, tar (for the installer's file copy) and
# a JRE/JDK for running servers.
PACKAGES=(
  python3
  python3-pip
  git
  tar
  zip
  unzip
  # OpenJDK 17 is a common baseline for modern Minecraft servers. Adjust as
  # needed for your server version (e.g. java-21-openjdk).
  java-17-openjdk-headless
)

$SUDO dnf install -y "${PACKAGES[@]}"

echo "[install_rocky9] OS packages installed."
