"""Java Runtime Manager — detect installed JREs and (optionally) install one.

This service backs the Java Runtime Manager screen. It does two things:

1. **Detection** (always available, read-only): probe the host for installed
   Java runtimes, work out which Java major versions are present, which ones the
   registered servers actually need, and which allowed versions are missing.

2. **Installation** (opt-in, privileged): when — and only when — the operator
   has wired up the minimal sudoers rules (see docs/16_java_installer.md), the
   GUI can ask us to install one of the allowed Java major versions. We never
   run a package manager directly and never accept a package name: we shell out
   to ``scripts/install_java_runtime.sh`` through ``sudo`` for ONE allowed major
   version. If the sudoers rule is absent, installation is reported as disabled.

Security invariants (see CLAUDE.md "Hard rules"):
  * The web app never runs as root and never runs arbitrary commands.
  * Only the allow-listed Java major versions (config.ALLOWED_JAVA_VERSIONS) are
    ever passed to the helper; anything else is rejected before sudo is touched.
  * No secrets are handled here, so nothing sensitive is logged.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app import config
from app.models import Server

# Candidate java binaries to probe, beyond whatever is first on PATH. Globs are
# expanded; non-existent paths are skipped. Covers the standard OpenJDK layout on
# both RHEL/Rocky (java-XX-openjdk) and Debian/Ubuntu (java-XX-openjdk-amd64).
_JVM_GLOBS = (
    "/usr/lib/jvm/*/bin/java",
    "/usr/lib/jvm/*/jre/bin/java",
)


# --- version-string parsing ----------------------------------------------------
def _parse_major(version_string: str) -> Optional[int]:
    """Extract the Java *major* version from a ``java -version`` line.

    Handles both the legacy ``1.8.0_392`` form (major = 8) and the modern
    ``17.0.10`` / ``21`` form (major = 17 / 21). Returns ``None`` if it cannot
    be parsed.
    """
    # The version literal is in quotes: e.g. openjdk version "17.0.10" 2024-...
    m = re.search(r'version "([^"]+)"', version_string)
    token = m.group(1) if m else version_string.strip()
    # Legacy 1.x.y -> the second component is the "real" major (1.8 -> 8).
    legacy = re.match(r"1\.(\d+)", token)
    if legacy:
        return int(legacy.group(1))
    modern = re.match(r"(\d+)", token)
    if modern:
        return int(modern.group(1))
    return None


def _probe_java(java_path: str) -> Optional[Dict]:
    """Run ``<java_path> -version`` and return a dict, or ``None`` on failure.

    Never raises — a binary that is missing, not executable or not actually a
    JVM simply yields ``None`` so detection keeps going.
    """
    try:
        out = subprocess.run(
            [java_path, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # java prints its version banner to stderr.
    text = (out.stderr or out.stdout or "").strip()
    if not text:
        return None
    first_line = text.splitlines()[0]
    return {
        "path": java_path,
        "major": _parse_major(first_line),
        "version_string": first_line,
    }


def detect_runtimes() -> List[Dict]:
    """Return the list of distinct Java runtimes found on this host.

    Each entry: ``{"path", "major", "version_string"}``. Deduplicated by the
    resolved (real) path so a binary reachable via several symlinks appears once.
    Sorted by major version (unknown majors last), then path.
    """
    candidates: List[str] = []

    # 1. Whatever 'java' resolves to on PATH, plus the registration-form default.
    for name in ("java", config.DEFAULT_JAVA_PATH):
        found = shutil.which(name) if not os.path.isabs(name) else (
            name if os.path.exists(name) else None
        )
        if found:
            candidates.append(found)

    # 2. The standard /usr/lib/jvm layout.
    for pattern in _JVM_GLOBS:
        candidates.extend(glob.glob(pattern))

    # Dedup by real path, preserving first-seen order.
    seen = set()
    runtimes: List[Dict] = []
    for path in candidates:
        try:
            real = os.path.realpath(path)
        except OSError:
            real = path
        if real in seen:
            continue
        seen.add(real)
        info = _probe_java(path)
        if info is not None:
            runtimes.append(info)

    runtimes.sort(key=lambda r: (r["major"] is None, r["major"] or 0, r["path"]))
    return runtimes


def installed_majors(runtimes: Optional[List[Dict]] = None) -> List[int]:
    """Sorted, de-duplicated list of installed Java major versions."""
    if runtimes is None:
        runtimes = detect_runtimes()
    majors = {r["major"] for r in runtimes if r["major"] is not None}
    return sorted(majors)


# --- required-Java inference ---------------------------------------------------
def minecraft_version_to_java(mc_version: Optional[str]) -> Optional[int]:
    """Best-effort: the Java major version a Minecraft version needs.

    Mapping (mapped onto the allowed set 8/17/21):
      * <= 1.16.x          -> 8
      * 1.17.x .. 1.20.4   -> 17
      * 1.20.5+ / 1.21+    -> 21

    Returns ``None`` when the version cannot be parsed (e.g. "Unknown").
    """
    if not mc_version:
        return None
    m = re.match(r"1\.(\d+)(?:\.(\d+))?", mc_version.strip())
    if not m:
        return None
    minor = int(m.group(1))
    patch = int(m.group(2)) if m.group(2) else 0
    if minor <= 16:
        return 8
    if minor < 20 or (minor == 20 and patch < 5):
        return 17
    return 21


def required_versions(db: Session) -> List[int]:
    """Java major versions actually required by the registered servers.

    Derived from each server's detected ``minecraft_version``. Falls back to the
    recommended version when nothing can be inferred (so the screen never shows
    an empty "Required Java").
    """
    required = set()
    for server in db.query(Server).all():
        major = minecraft_version_to_java(server.minecraft_version)
        if major is not None:
            required.add(major)
    if not required:
        required.add(config.JAVA_RECOMMENDED_VERSION)
    # Only surface versions we actually know how to install/recommend.
    return sorted(v for v in required if v in config.ALLOWED_JAVA_VERSIONS)


# --- OS / package helpers ------------------------------------------------------
def os_family() -> str:
    """Return 'rhel' (dnf), 'debian' (apt) or 'unknown'."""
    if shutil.which("dnf"):
        return "rhel"
    if shutil.which("apt-get"):
        return "debian"
    return "unknown"


def package_name(version: int, family: Optional[str] = None) -> Optional[str]:
    """The OS package that provides Java ``version`` on this host, or ``None``.

    Mirrors scripts/install_java_runtime.sh exactly.
    """
    if version not in config.ALLOWED_JAVA_VERSIONS:
        return None
    family = family or os_family()
    if family == "rhel":
        return "java-1.8.0-openjdk-headless" if version == 8 else (
            f"java-{version}-openjdk-headless"
        )
    if family == "debian":
        return f"openjdk-{version}-jre-headless"
    return None


def manual_command(version: int, family: Optional[str] = None) -> Optional[str]:
    """A copy-pasteable manual install command for ``version`` (or ``None``).

    Shown in the UI so an operator can install Java by hand when the GUI
    installer is disabled, or when the package is not available and they want to
    use a different source.
    """
    family = family or os_family()
    pkg = package_name(version, family)
    if pkg is None:
        return None
    if family == "rhel":
        return f"sudo dnf install -y {pkg}"
    if family == "debian":
        return f"sudo apt-get update && sudo apt-get install -y {pkg}"
    return None


# --- installer enablement (sudoers probe) -------------------------------------
def _script_path() -> str:
    return str(config.JAVA_INSTALLER_SCRIPT)


def can_install(version: int) -> bool:
    """Whether the GUI may install ``version`` right now (non-mutating probe).

    True only when:
      * the version is allowed,
      * ``sudo`` exists and the helper script is present, and
      * a sudoers rule permits THIS user to run the helper for THIS version
        without a password.

    We discover the last point with ``sudo -n -l`` (list mode), which reports
    whether a command is permitted WITHOUT running it and WITHOUT prompting
    (``-n`` = non-interactive). If no rule is configured this returns non-zero,
    so installation is reported as disabled — matching the "GUI install is not
    enabled" state shown in the UI.
    """
    if version not in config.ALLOWED_JAVA_VERSIONS:
        return False
    if not shutil.which("sudo"):
        return False
    script = _script_path()
    if not os.path.exists(script):
        return False
    try:
        result = subprocess.run(
            ["sudo", "-n", "-l", script, str(version)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def installer_enabled() -> bool:
    """True if the GUI installer is usable for at least one allowed version."""
    return any(can_install(v) for v in config.ALLOWED_JAVA_VERSIONS)


# --- install execution ---------------------------------------------------------
def _append_log(text: str) -> None:
    """Best-effort append of install output to the app-side java_install.log.

    Never raises: a logging failure must not turn a successful install into an
    error in the UI.
    """
    try:
        with open(config.JAVA_INSTALL_LOG, "a", encoding="utf-8") as fh:
            fh.write(text)
            if not text.endswith("\n"):
                fh.write("\n")
    except OSError:
        pass


def install_java(version: int) -> Dict:
    """Install Java ``version`` via the privileged helper. Returns a result dict.

    Result shape (always JSON-serialisable, never raises):
        {
          "ok": bool,
          "version": int,
          "message": str,
          "returncode": Optional[int],
          "stdout": str,
          "stderr": str,
          "command": str,          # display-only argv, no secrets
        }
    """
    # Defence in depth: reject disallowed versions before touching sudo. The
    # router validates too, but this service must be safe on its own.
    if version not in config.ALLOWED_JAVA_VERSIONS:
        return {
            "ok": False,
            "version": version,
            "message": f"Java {version} is not an allowed version.",
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "command": "",
        }

    if not can_install(version):
        return {
            "ok": False,
            "version": version,
            "message": (
                "GUI install is not enabled. Ask an administrator to enable the "
                "Java installer (see docs/16_java_installer.md) or install Java "
                "manually."
            ),
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "command": "",
        }

    script = _script_path()
    argv = ["sudo", "-n", script, str(version)]
    display_cmd = " ".join(argv)

    header = (
        f"\n===== {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC "
        f"install Java {version} =====\n$ {display_cmd}\n"
    )
    _append_log(header)

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=config.JAVA_INSTALL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or "") + (exc.stderr or "")
        _append_log(partial + f"\n[timed out after {config.JAVA_INSTALL_TIMEOUT_SECONDS}s]\n")
        return {
            "ok": False,
            "version": version,
            "message": (
                f"Install timed out after {config.JAVA_INSTALL_TIMEOUT_SECONDS}s. "
                "It may still be running on the host; rescan in a moment."
            ),
            "returncode": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "command": display_cmd,
        }
    except OSError as exc:
        _append_log(f"[failed to launch: {exc}]\n")
        return {
            "ok": False,
            "version": version,
            "message": f"Could not start the installer: {exc}",
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "command": display_cmd,
        }

    _append_log((proc.stdout or "") + (proc.stderr or "") + f"\n[exit {proc.returncode}]\n")

    ok = proc.returncode == 0
    if ok:
        message = f"Java {version} installed successfully."
    else:
        message = (
            f"Install failed (exit {proc.returncode}). The package may not be "
            "available for this distribution — see the output below."
        )
    return {
        "ok": ok,
        "version": version,
        "message": message,
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "command": display_cmd,
    }


# --- assembled view for the API / template ------------------------------------
def get_java_info(db: Session) -> Dict:
    """Assemble the full Java Runtime Manager payload (read-only)."""
    runtimes = detect_runtimes()
    installed = installed_majors(runtimes)
    required = required_versions(db)
    family = os_family()
    enabled = installer_enabled()

    versions: List[Dict] = []
    for v in config.ALLOWED_JAVA_VERSIONS:
        versions.append(
            {
                "version": v,
                "installed": v in installed,
                "required": v in required,
                "recommended": v == config.JAVA_RECOMMENDED_VERSION,
                "can_install": can_install(v) if v not in installed else False,
                "package": package_name(v, family),
                "manual_command": manual_command(v, family),
            }
        )

    not_installed = [v for v in config.ALLOWED_JAVA_VERSIONS if v not in installed]

    return {
        "runtimes": runtimes,
        "installed_versions": installed,
        "recommended_version": config.JAVA_RECOMMENDED_VERSION,
        "required_versions": required,
        "not_installed_versions": not_installed,
        "allowed_versions": list(config.ALLOWED_JAVA_VERSIONS),
        "installer_enabled": enabled,
        "os_family": family,
        "versions": versions,
    }
