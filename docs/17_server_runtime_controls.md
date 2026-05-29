# 17 · Server Runtime Controls (v0.5)

This document covers the v0.5 "server runtime controls" work, which tightens
four related areas: **per-server Java runtime selection**, a **safer Stop**, an
easier **RCON password** flow, and a more **accurate runtime-status** display.

Everything here stays inside the v0.1 security model (see
[`03_security_policy.md`](03_security_policy.md) and `CLAUDE.md`): the web app
never runs as root, never runs arbitrary commands, never edits files outside a
registered `server_path`, and never logs the RCON password.

---

## 1 · Per-server Java runtime selection

Each `Server` row already has a `java_path`; v0.5 lets you change it from the
GUI instead of editing the database by hand.

**Server detail → Java Runtime** shows:

- the current `java_path` and the Java version detected from it,
- the Java version this server's Minecraft version **requires** (inferred from
  `minecraft_version`),
- the **recommended** Java and whether it is installed,
- a **Java mismatch** warning when the selected runtime is too old (or the path
  is not a working JVM), and
- the list of Java runtimes detected on the host.

You can:

- pick any **detected** runtime from a dropdown and **Save Java runtime**, or
- click **Use Java N for this server** when the recommended runtime is installed.

The version → Java mapping (`java_runtime.minecraft_version_to_java`):

| Minecraft version        | Java |
|--------------------------|------|
| ≤ 1.16.x                 | 8    |
| 1.17.x – 1.20.4          | 17   |
| 1.20.5+ / 1.21+          | 21   |
| new "year" scheme (e.g. 26.1.2) | 25 |

**Create Server** now pre-selects the recommended runtime if it is installed,
and otherwise shows a warning plus a link to the Java Runtime Manager.

### Guard rails

- The API only ever accepts a `java_path` that matches one of the runtimes we
  actually **detected** (`java_runtime.is_known_runtime_path`). Because
  `java_path` is passed to `subprocess.Popen` at launch, this prevents pointing
  a server at an arbitrary binary through the GUI.
- We **never** touch the system-wide `/usr/bin/java` `alternatives` link — only
  the per-server `java_path` changes.

---

## 2 · Safe stop (RCON → stdin → SIGTERM)

`server_process.stop_server` now tries the gentlest method that can reach the
server, in order, and records which one worked:

1. **RCON** `save-all flush` + `stop` (only when RCON is enabled); waits up to
   `RCON_STOP_TIMEOUT_SECONDS`.
2. **stdin** — writes `stop\n` to the server's console via a **FIFO** that we
   attach as the server's stdin at launch; waits up to
   `STDIN_STOP_TIMEOUT_SECONDS`.
3. **SIGTERM**; waits up to `STOP_TIMEOUT_SECONDS`.

If all three fail, the stop is reported as **failed** — `SIGKILL` is **never**
sent automatically.

### How the stdin path works

At launch (`start_server`) the server is started with its stdin connected to a
FIFO under `MANAGED_LOG_DIR` (`<sanitised-name>.stdin`). The FIFO is opened
`O_RDWR` so the open file description always has a writer; the server therefore
never sees an immediate EOF and its console reader stays alive. To stop, we open
the FIFO `O_WRONLY | O_NONBLOCK` and write `stop\n`:

- If there is **no reader** (a server launched by a pre-v0.5 version with
  `DEVNULL` stdin, or one that closed its console), the open fails with `ENXIO`
  and we fall back to SIGTERM — fully backward compatible.
- The FIFO path is sanitised and confined to `MANAGED_LOG_DIR`, like the managed
  log, so a crafted server name cannot escape it.

### Stop method shown in the UI / log

The result is recorded on the server row (`last_stop_method`, `last_stopped_at`,
`last_stop_error`) and appended to the managed log. The UI shows one of:

- **Stopped by RCON** (`rcon`)
- **Stopped by stdin command** (`stdin`)
- **Stopped by SIGTERM** (`sigterm`)
- **Failed to stop** (`failed`)
- **Force-killed** (`sigkill`, only via the Kill button)

### Kill button (the only SIGKILL)

A separate, **confirmation-guarded** **✕ Kill** button (`POST /servers/{id}/kill`
→ `kill_server`) is the *only* path that sends `SIGKILL`. It does **not** save
the world and is intended only when a normal Stop has failed.

---

## 3 · RCON password generate / apply

**Server detail → RCON settings**:

- **Generate & enable RCON** fills a strong random password
  (`rcon_service.generate_password`, 20 chars of `[A-Za-z0-9]`), ticks
  `enable-rcon`, and sets `rcon.port=25575` when the port field is empty.
- The password field is **masked** (`type=password`) with a **Show / Hide**
  toggle; the freshly generated value is revealed once so you can note it.
- Generating over an existing password asks for **confirmation** first.
- **Save RCON settings** writes `enable-rcon` / `rcon.port` / `rcon.password`
  into `server.properties` (a `.bak` is made) and mirrors `rcon_enabled` /
  `rcon_host` / `rcon_port` / `rcon_password` into the database.
- A reminder is shown that the **Minecraft server must be restarted** for RCON
  changes to take effect (RCON settings are only read at startup).

The password is generated server-side and only ever travels back to the browser
once (for review); it is **never** placed in a redirect URL, the managed log, or
any application log.

---

## 4 · Runtime-status accuracy

`server_console.evaluate` / `_classify` now distinguish:

`stopped` · `starting` · `running` · `stopping` · `crashed` ·
`failed_to_start` · `unknown`

Decision inputs:

- the real process (PID + `cwd` check, as before),
- whether the configured **server port** is `LISTEN`ing (psutil; `None` if it
  can't be checked),
- log markers in the recent tail of `latest.log` **and** the managed log:
  - **completion**: `Done (…s)!` (also yields the **startup duration**),
  - **stopping**: `Stopping the server` / `Stopping server`,
  - **specific faults** (each mapped to a short summary):
    `UnsupportedClassVersionError`, EULA-not-accepted, `Address already in use`
    / `FAILED TO BIND TO PORT`, `OutOfMemoryError`, `Permission denied`,
  - generic faults (`Exception` / `ERROR` / `Crash`) as a fallback.

Rough rules:

- alive + `Stopping …` → **stopping**
- alive + `Done (…)` → **running** (port-listen state appended to the reason)
- alive + a specific/generic fault before completion → **failed_to_start** /
  **crashed**
- alive, nothing yet → **starting**
- dead + fault before completion → **failed_to_start**
- dead + DB still "running"/"starting" → **crashed**
- dead, clean → **stopped**

The detail page and Console show: status badge, last start / stop times, last
stop method, last error summary, startup duration, and port status. The detected
state and startup duration are persisted (`last_runtime_status`,
`last_startup_duration_seconds`) so they survive without live polling (written
only when they change).

---

## 5 · Restart flow

`restart_server` now: **safe-stop → confirm stopped → start → report**. If the
stop does not complete, it **does not start** (and says so). Each step is written
to the managed log so the sequence is auditable. A start failure records
`last_start_error`.

---

## 6 · Database

`servers` gains (nullable; added by the simple migration in `database.py`, no
Alembic):

| column | meaning |
|--------|---------|
| `last_started_at` | last successful start (UTC) |
| `last_stopped_at` | last successful stop (UTC) |
| `last_stop_method` | `rcon` / `stdin` / `sigterm` / `sigkill` / `failed` |
| `last_stop_error` | short last-stop error/trace (no secrets) |
| `last_start_error` | short last-start error |
| `last_runtime_status` | last detected console state |
| `last_startup_duration_seconds` | parsed from the `Done (…)` line |

None of these ever hold the RCON password.

---

## Known limitations

- The **stdin stop** only works for servers launched by **v0.5+** (they get the
  FIFO stdin). A server already running from an older version falls back to
  SIGTERM until it is next started.
- **Port-listen** detection relies on `psutil.net_connections`; on some hosts
  this needs privileges to see other users' sockets. The web app runs as a
  normal user, so it reliably sees only sockets owned by that user (which
  includes servers it launched). When it can't tell, the port state shows as
  unknown and does not change the detected state.
- State detection is **heuristic** — it scans the recent log tail, so a marker
  that has scrolled out of the scanned window (`CONSOLE_STATE_SCAN_LINES`) is no
  longer considered.
- Java **version inference** is best-effort and only covers the allowed set
  (8/17/21/25); an unknown Minecraft version yields no required-Java and falls
  back to the recommended version.
- `SIGKILL` remains **manual only** (the Kill button); there is still no
  automatic force-kill, and no file/server deletion.
