"""RCON client and command allow-list for v0.2 (admin-only).

This service is the single place that talks to a Minecraft server's RCON
endpoint. It is used to send a fixed, *allow-listed* set of console commands, to
run a connection test, and to perform a graceful ``save-all`` + ``stop`` during
shutdown and backups.

Library choice
--------------
We use the third-party ``rcon`` package (Source RCON over plain TCP). It was
chosen over the also-popular ``mcrcon`` because mcrcon implements its connection
timeout with ``signal.SIGALRM``, which only works on the main thread. FastAPI
runs our synchronous route handlers (and the stop path) in a worker threadpool,
where ``signal.signal`` raises ``ValueError``. ``rcon`` instead relies on plain
socket timeouts, so it is safe to call from any thread. It is small, dependency
free and actively maintained. See docs/09_rcon.md for details.

Security model (v0.2)
---------------------
* Only commands in :data:`ALLOWED_COMMANDS` may be sent — there is no arbitrary
  console input field (a guarded "advanced" mode is left for a later version).
* Every argument (player names, messages) is validated/sanitised before it is
  put on the wire — see :func:`validate_command`.
* The RCON password is never logged and never returned to the UI in plaintext.
"""
from __future__ import annotations

import re
import secrets
import socket
import string
from typing import Dict, List, Optional, Tuple

from app.config import (
    DEFAULT_RCON_HOST,
    DEFAULT_RCON_PORT,
    RCON_CONNECT_TIMEOUT_SECONDS,
)
from app.models import Server

# Minecraft (Java) usernames are 3-16 chars of [A-Za-z0-9_]; we accept 1-16 to
# be lenient with bedrock/geyser-style names while still rejecting anything that
# could smuggle whitespace or control characters onto the command line.
_PLAYER_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
_MAX_TEXT = 200

# Human-readable list of what the v0.2 console accepts (shown in the UI and docs).
ALLOWED_COMMANDS: List[str] = [
    "list",
    "say <message>",
    "save-all",
    "save-all flush",
    "whitelist list",
    "whitelist add <player>",
    "whitelist remove <player>",
    "op <player>",
    "deop <player>",
    "kick <player> <reason>",
    "stop",
]


class RconError(Exception):
    """An RCON failure carrying a short message and an operator-facing hint."""

    def __init__(self, message: str, diagnosis: str):
        super().__init__(message)
        self.message = message
        self.diagnosis = diagnosis


def _result(
    ok: bool,
    message: str,
    response: Optional[str] = None,
    diagnosis: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """Build the dict returned to routers (JSON-serialisable, Jinja-friendly)."""
    return {"ok": ok, "message": message, "response": response, "diagnosis": diagnosis}


def _clean_text(text: str) -> str:
    """Strip control characters (incl. newlines) and cap length for free text.

    Used for the ``say`` message and ``kick`` reason so a caller cannot inject
    extra protocol/console lines or unbounded payloads.
    """
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", text or "").strip()
    return cleaned[:_MAX_TEXT]


def validate_command(raw: str) -> Tuple[Optional[str], Optional[str]]:
    """Validate a console command against the v0.2 allow-list.

    Returns ``(normalised_command, None)`` when allowed, or ``(None, error)``
    when rejected. This is the chokepoint guaranteeing only known, safe commands
    ever reach the server — every caller that sends user input goes through it.
    """
    cmd = (raw or "").strip()
    if not cmd:
        return None, "Empty command."

    lowered = " ".join(cmd.lower().split())  # collapse internal whitespace
    parts = cmd.split()
    head = parts[0].lower()

    # Fixed, argument-free commands.
    if lowered in ("list", "save-all", "save-all flush", "whitelist list", "stop"):
        return lowered, None

    if head == "say":
        message = _clean_text(cmd[len(parts[0]):])
        if not message:
            return None, "'say' requires a message."
        return f"say {message}", None

    if head == "kick":
        if len(parts) < 2 or not _PLAYER_RE.match(parts[1]):
            return None, "'kick' requires a valid player name."
        reason = _clean_text(" ".join(parts[2:]))
        return f"kick {parts[1]} {reason}".strip(), None

    if head in ("op", "deop"):
        if len(parts) != 2 or not _PLAYER_RE.match(parts[1]):
            return None, f"'{head}' requires a single valid player name."
        return f"{head} {parts[1]}", None

    if head == "whitelist" and len(parts) == 3 and parts[1].lower() in ("add", "remove"):
        if not _PLAYER_RE.match(parts[2]):
            return None, "'whitelist add/remove' requires a valid player name."
        return f"whitelist {parts[1].lower()} {parts[2]}", None

    return None, "Command is not in the v0.2 allow-list."


def generate_password(length: int = 20) -> str:
    """Return a strong random RCON password.

    Restricted to letters and digits so it is safe to store verbatim in
    server.properties (no ``=``/whitespace/quote characters that the simple
    properties parser could choke on).
    """
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _connection_target(server: Server) -> Tuple[str, int, str]:
    host = (server.rcon_host or DEFAULT_RCON_HOST).strip() or DEFAULT_RCON_HOST
    try:
        port = int(server.rcon_port or DEFAULT_RCON_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_RCON_PORT
    return host, port, server.rcon_password or ""


def _run_commands(server: Server, commands: List[str]) -> List[str]:
    """Open one RCON connection, run each command, return the responses.

    Raises :class:`RconError` (with a diagnosis) on any failure. The connection
    is always closed by the context manager, including after ``stop``.
    """
    try:
        from rcon.exceptions import SessionTimeout, WrongPassword
        from rcon.source import Client
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RconError(
            "RCON support library is not installed.",
            "Run 'pip install -r requirements.txt' in the app's virtualenv to "
            "enable RCON.",
        ) from exc

    host, port, password = _connection_target(server)
    if not password:
        raise RconError(
            "RCON password is not configured.",
            "Set an RCON password (and enable-rcon) in RCON settings, then "
            "restart the Minecraft server.",
        )

    responses: List[str] = []
    try:
        with Client(host, port, passwd=password, timeout=RCON_CONNECT_TIMEOUT_SECONDS) as client:
            for command in commands:
                responses.append(client.run(command))
    except WrongPassword as exc:
        raise RconError(
            "RCON authentication failed (wrong password).",
            "The password here does not match rcon.password in server.properties. "
            "Re-enter it under RCON settings and restart the server.",
        ) from exc
    except ConnectionRefusedError as exc:
        raise RconError(
            "Connection refused by the server.",
            "The Minecraft server may be stopped, enable-rcon may be false, or "
            "rcon.port may not match. Start the server and verify enable-rcon / "
            "rcon.port, then restart it.",
        ) from exc
    except (socket.timeout, SessionTimeout, TimeoutError) as exc:
        raise RconError(
            "RCON connection timed out.",
            "No response in time — check rcon.port, a firewall between the app "
            "and the server, or whether the server has finished starting.",
        ) from exc
    except OSError as exc:
        # gaierror (bad host), network unreachable, connection reset (e.g. the
        # socket dropping as the server shuts down after `stop`), etc.
        raise RconError(
            f"RCON connection error: {exc}",
            "Check the connect host, rcon.port and any firewall, and confirm the "
            "server is reachable from this machine.",
        ) from exc
    return responses


def test_connection(server: Server) -> Dict[str, Optional[str]]:
    """Connect and run ``list`` as a health check, with failure diagnosis."""
    try:
        responses = _run_commands(server, ["list"])
    except RconError as exc:
        return _result(False, exc.message, diagnosis=exc.diagnosis)
    return _result(True, "RCON connection OK (ran 'list').", response=responses[0])


def run_command(server: Server, raw_command: str) -> Dict[str, Optional[str]]:
    """Validate ``raw_command`` against the allow-list and run it over RCON."""
    normalized, err = validate_command(raw_command)
    if err:
        return _result(False, err)

    try:
        responses = _run_commands(server, [normalized])
    except RconError as exc:
        # Sending `stop` legitimately drops the connection as the server exits.
        if normalized == "stop" and (
            "connection" in exc.message.lower() or "timed out" in exc.message.lower()
        ):
            return _result(True, "Sent 'stop'; the server is shutting down.")
        return _result(False, exc.message, diagnosis=exc.diagnosis)
    return _result(True, f"Sent: {normalized}", response=responses[0])


def save_all(server: Server, flush: bool = True) -> Dict[str, Optional[str]]:
    """Run ``save-all flush`` (preferred) or ``save-all`` to flush the world."""
    command = "save-all flush" if flush else "save-all"
    try:
        responses = _run_commands(server, [command])
    except RconError as exc:
        return _result(False, f"{command} failed: {exc.message}", diagnosis=exc.diagnosis)
    return _result(True, f"{command} executed.", response=responses[0])


def stop_via_rcon(server: Server) -> Dict[str, Optional[str]]:
    """Flush the world then stop the server, over RCON.

    ``save-all flush`` and ``stop`` use separate connections so we can tell which
    step failed. A connection drop/timeout *during stop* is expected (the server
    closes the socket as it exits) and is reported as success.
    """
    try:
        _run_commands(server, ["save-all flush"])
    except RconError as exc:
        return _result(False, f"save-all failed: {exc.message}", diagnosis=exc.diagnosis)

    try:
        _run_commands(server, ["stop"])
    except RconError as exc:
        if "connection" in exc.message.lower() or "timed out" in exc.message.lower():
            return _result(True, "RCON save-all done; 'stop' issued (connection closed).")
        return _result(False, f"stop failed: {exc.message}", diagnosis=exc.diagnosis)
    return _result(True, "RCON save-all + stop issued.")
