"""Detect information about an existing Minecraft server directory."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from app.schemas import DetectionResult


def _guess_type(jar_name: str) -> str:
    """Very simple jar-name based server-type heuristic."""
    name = jar_name.lower()
    # Order matters: neoforge before forge so it is not swallowed by 'forge'.
    if "paper" in name:
        return "Paper"
    if "fabric" in name:
        return "Fabric"
    if "neoforge" in name:
        return "NeoForge"
    if "forge" in name:
        return "Forge"
    return "Unknown"


_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


def _guess_version(jar_name: str) -> Optional[str]:
    """Best-effort version extraction from the jar filename."""
    match = _VERSION_RE.search(jar_name)
    return match.group(1) if match else None


def detect_server(server_path: str) -> DetectionResult:
    """Scan ``server_path`` and report what was found.

    This is read-only and never raises for a missing directory; an empty
    result is returned instead so the caller can decide how to react.
    """
    result = DetectionResult()
    base = Path(server_path)
    if not base.is_dir():
        return result

    # Collect top-level jar candidates (non-recursive: server jars live at root).
    jars = sorted(p.name for p in base.glob("*.jar") if p.is_file())
    result.jar_candidates = jars

    result.has_server_properties = (base / "server.properties").is_file()
    result.has_eula = (base / "eula.txt").is_file()
    result.has_latest_log = (base / "logs" / "latest.log").is_file()
    result.has_world = (base / "world").is_dir()
    result.has_mods = (base / "mods").is_dir()
    result.has_plugins = (base / "plugins").is_dir()
    result.has_config = (base / "config").is_dir()

    if jars:
        # Prefer the first jar whose name hints at a known type, else first jar.
        chosen = jars[0]
        for jar in jars:
            if _guess_type(jar) != "Unknown":
                chosen = jar
                break
        result.minecraft_type = _guess_type(chosen)
        result.minecraft_version = _guess_version(chosen)

    return result
