"""Host metrics and system information via psutil / stdlib."""
from __future__ import annotations

import platform
import shutil
import socket
import subprocess
from typing import Dict

import psutil

from app.config import BASE_DIR


def get_metrics() -> Dict[str, float]:
    """Return CPU / memory / disk usage percentages for the dashboard."""
    # interval=None returns usage since the previous call (non-blocking).
    cpu = psutil.cpu_percent(interval=0.3)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(str(BASE_DIR))
    return {
        "cpu_percent": round(cpu, 1),
        "mem_percent": round(mem.percent, 1),
        "mem_used_gb": round(mem.used / (1024 ** 3), 2),
        "mem_total_gb": round(mem.total / (1024 ** 3), 2),
        "disk_percent": round(disk.percent, 1),
        "disk_used_gb": round(disk.used / (1024 ** 3), 2),
        "disk_total_gb": round(disk.total / (1024 ** 3), 2),
    }


def _java_version(java_path: str = "/usr/bin/java") -> str:
    """Best-effort java version string; never raises."""
    if not shutil.which(java_path) and not shutil.which("java"):
        return "not found"
    try:
        # java prints its version to stderr.
        out = subprocess.run(
            [java_path, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        text = out.stderr or out.stdout
        return text.strip().splitlines()[0] if text.strip() else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _primary_ip() -> str:
    """Determine the host's primary outbound IP without sending packets."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # No traffic is actually sent for a UDP connect.
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


def get_system_info() -> Dict[str, str]:
    """Return OS / Python / Java / disk / IP info for the System page."""
    disk = psutil.disk_usage(str(BASE_DIR))
    return {
        "os": f"{platform.system()} {platform.release()}",
        "os_detail": platform.platform(),
        "hostname": socket.gethostname(),
        "python_version": platform.python_version(),
        "java_version": _java_version(),
        "ip_address": _primary_ip(),
        "disk_total_gb": f"{disk.total / (1024 ** 3):.1f}",
        "disk_used_gb": f"{disk.used / (1024 ** 3):.1f}",
        "disk_free_gb": f"{disk.free / (1024 ** 3):.1f}",
        "cpu_count": str(psutil.cpu_count(logical=True)),
    }
