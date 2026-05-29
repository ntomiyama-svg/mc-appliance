#!/usr/bin/env bash
#
# mc-appliance — privileged Java runtime installer helper.
#
# This is the ONLY thing the (unprivileged) web app is permitted to run as root,
# and ONLY via a minimal sudoers rule per allowed version (see
# docs/16_java_installer.md). It deliberately accepts a SINGLE argument — the
# Java *major* version — and nothing else:
#
#     install_java_runtime.sh <8|11|17|21|25>
#
# Security model (why this script exists at all):
#   * The web app must never run as root and must never run arbitrary commands
#     or install arbitrary packages (see CLAUDE.md "Hard rules"). Instead it
#     shells out to THIS script through sudo, which:
#       - hard-codes the allow-list of Java major versions,
#       - hard-codes the package name per OS family (no caller-supplied names),
#       - never reads MORE than its one positional argument.
#   * The matching sudoers entries are per-version exact-match commands, so even
#     a compromised web app can only ever request one of the five known
#     installs — never a free-form package or command.
#
# Output: everything is echoed to stdout/stderr so the caller (and the GUI) can
# show progress. We additionally best-effort append to a log file (running as
# root we can usually write /var/log/mc-appliance); failure to log is never
# fatal. No secrets are handled or printed by this script.
#
# Exit codes:
#   0  package installed (or already present)
#   2  usage / disallowed version
#   3  no supported package manager (dnf/apt) found
#   4  package not available for this distribution / install failed
set -euo pipefail

# Allowed Java major versions. Keep in sync with
# app/services/java_runtime.ALLOWED_JAVA_VERSIONS and the sudoers rules.
ALLOWED_VERSIONS="8 11 17 21 25"

# Where to additionally record install output. Overridable for dev, but note
# that sudo's env_reset usually drops this — so in practice the default below is
# what gets used under the real (root, via sudo) path. Best-effort only.
LOG_FILE="${MCAPP_JAVA_LOG:-/var/log/mc-appliance/java_install.log}"

log() {
  # Echo to stderr (so it is visible even when stdout is captured as data) and
  # best-effort append to the log file. Never fail because logging failed.
  local line="[install_java_runtime] $*"
  echo "$line" >&2
  if [[ -n "${LOG_FILE:-}" ]]; then
    local dir
    dir="$(dirname "$LOG_FILE")"
    if [[ -d "$dir" && -w "$dir" ]] || { [[ -f "$LOG_FILE" && -w "$LOG_FILE" ]]; }; then
      printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$line" >>"$LOG_FILE" 2>/dev/null || true
    fi
  fi
}

usage() {
  echo "Usage: $(basename "$0") <${ALLOWED_VERSIONS// /|}>" >&2
}

# --- 1. Validate the single argument ------------------------------------------
if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

VERSION="$1"

# Strictly numeric and on the allow-list. Reject anything else (no package
# names, no flags, no extra tokens).
allowed=0
for v in $ALLOWED_VERSIONS; do
  if [[ "$VERSION" == "$v" ]]; then
    allowed=1
    break
  fi
done
if [[ "$allowed" -ne 1 ]]; then
  echo "ERROR: Java version '$VERSION' is not allowed. Allowed: $ALLOWED_VERSIONS" >&2
  usage
  exit 2
fi

log "Requested install of Java $VERSION (uid=$(id -u))."

# --- 2. Detect the OS package manager and the package name --------------------
# Package names are hard-coded per OS family + version. The caller never gets to
# influence these beyond choosing one of the five allowed major versions.
PKG=""
MANAGER=""

if command -v dnf >/dev/null 2>&1; then
  MANAGER="dnf"
  case "$VERSION" in
    8)  PKG="java-1.8.0-openjdk-headless" ;;
    11) PKG="java-11-openjdk-headless" ;;
    17) PKG="java-17-openjdk-headless" ;;
    21) PKG="java-21-openjdk-headless" ;;
    25) PKG="java-25-openjdk-headless" ;;
  esac
elif command -v apt-get >/dev/null 2>&1; then
  MANAGER="apt"
  # Debian/Ubuntu use a uniform openjdk-XX-jre-headless naming.
  PKG="openjdk-${VERSION}-jre-headless"
else
  echo "ERROR: no supported package manager found (need dnf or apt)." >&2
  exit 3
fi

log "Detected package manager: $MANAGER. Target package: $PKG"

# --- 3. Install ---------------------------------------------------------------
# We do NOT switch the system 'alternatives'/default java — installing a runtime
# is additive. mc-appliance selects the java binary per server via java_path.
set +e
if [[ "$MANAGER" == "dnf" ]]; then
  log "Running: dnf install -y $PKG"
  dnf install -y "$PKG"
  rc=$?
else
  export DEBIAN_FRONTEND=noninteractive
  log "Running: apt-get update"
  apt-get update
  log "Running: apt-get install -y $PKG"
  apt-get install -y "$PKG"
  rc=$?
fi
set -e

if [[ "$rc" -ne 0 ]]; then
  echo "ERROR: failed to install '$PKG' via $MANAGER (exit $rc)." >&2
  echo "       This Java version may not be packaged for this distribution." >&2
  echo "       For example, Java 25 is not yet shipped by every distro." >&2
  exit 4
fi

log "Successfully installed '$PKG' (Java $VERSION)."
echo "OK: Java $VERSION installed ($PKG via $MANAGER)."
exit 0
