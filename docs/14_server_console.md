# 14 · Server Console / Live Log Viewer (v0.5)

The **Console** section on the server detail page (and the standalone
`/servers/<id>/console` page) lets an operator see, from the browser, whether a
Minecraft server is *actually* up: its PID, the real process state, both log
sources, and a derived health verdict. When RCON is enabled it also offers the
allow-listed command box.

This feature adds **no new privileges**. It runs as the same unprivileged web
user, reads only two well-defined files, never deletes anything, and never shows
the RCON password.

## The two log sources (and why they differ)

| Source | File | Written by | What it shows |
| --- | --- | --- | --- |
| **Minecraft `latest.log`** | `<server_path>/logs/latest.log` | the Minecraft server itself | the game/server's own structured log (tick errors, joins, `Done (…)`) |
| **Managed process log** | `<MANAGED_LOG_DIR>/<sanitized_name>.log` | mc-appliance | the raw **stdout/stderr** of the process *we* launched, plus a start banner |

Why both:

- `latest.log` only exists once the server has written it; a server that dies
  *before* logging (bad jar, wrong Java, out-of-memory at JVM start) may leave
  `latest.log` empty or absent, while the **managed log** still captures the JVM
  error on stderr.
- Conversely, a server started **outside** mc-appliance (e.g. registered while
  already running) has a `latest.log` but no managed log for that run.

Seeing them side-by-side is the quickest way to tell "is it the JVM or the game
that failed?".

### Managed log location

`MANAGED_LOG_DIR` is `<LOG_DIR>/server_logs`:

- **dev:** `./data/server_logs/<name>.log` (since `LOG_DIR` defaults to
  `DATA_DIR`). To match the requested layout exactly, set
  `MC_APPLIANCE_LOG_DIR=./logs` so it becomes `./logs/server_logs/<name>.log`.
- **prod (systemd):** `MC_APPLIANCE_LOG_DIR=/var/log/mc-appliance` →
  `/var/log/mc-appliance/server_logs/<name>.log`.

The filename is the server name passed through `sanitize_server_name()`
(`[^A-Za-z0-9_-]` → `_`, falling back to `server`), so a crafted name can never
introduce a path separator or `..`. The resolved path is additionally checked to
be a direct child of `MANAGED_LOG_DIR`. For backward compatibility the reader
falls back to the pre-v0.5 `server_<id>.log` name if the new file is absent.

On start, `server_process.start_server` appends a small banner to the managed
log — the launch command, cwd and PID — and the start time. The command is only
`java`, the `-Xms/-Xmx` flags and the jar name; **no secret is ever written**
(the RCON password is not part of the launch command).

## State detection

`services/server_console.evaluate()` combines the real process state with a scan
of the last `CONSOLE_STATE_SCAN_LINES` (250) lines of *both* logs. It does **not**
change the persisted `status` column — that keeps its v0.1 `running`/`stopped`
meaning so the existing Start/Stop/refresh logic and badges are unaffected. The
detected state is a separate, read-only verdict.

Markers scanned for:

- **startup-complete:** `Done (`, `For help, type`, `Server thread/INFO`
- **error/fault:** `Exception`, `ERROR`, `Failed`, `Crash`, `Could not`,
  `Unable to`

The state machine (`_classify`):

| detected_state | condition |
| --- | --- |
| `stopped` | no PID / process not running, DB not "running", no error lines |
| `starting` | process alive, no startup-complete line yet |
| `running` | process alive **and** a startup-complete line is present |
| `crashed` | DB says running/starting but the process is gone (unexpected exit), **or** a process-gone state with error lines, **or** process alive with error lines but no startup-complete marker (failed startup) |
| `unknown` | the process state could not be determined |

`startup-complete` wins over `error` for a live process, because a healthy
running server still logs the occasional error.

## API

All routes are admin-only (the global `AuthMiddleware`). They live under
`/servers/<id>/console/...` — *not* `/api/...` — so an unauthenticated request is
bounced to `/login` with a `303` redirect (the front-end detects this and sends
the browser to the login page).

| Method · path | Returns |
| --- | --- |
| `GET /servers/{id}/console` | standalone Console page (the same section is embedded in the detail page) |
| `GET /servers/{id}/console/status` | JSON: `db_status`, `actual_status`, `pid`, `process_exists`, `latest_log_exists`, `managed_log_exists`, `latest_log_mtime`, `managed_log_mtime`, `detected_state`, `detected_reason` |
| `GET /servers/{id}/console/latest-log?lines=` | JSON `{ok, exists, text, lines, mtime}` — tail of `logs/latest.log` |
| `GET /servers/{id}/console/managed-log?lines=` | JSON `{ok, exists, text, lines, mtime}` — tail of the managed log |
| `POST /servers/{id}/console/rcon-command` | JSON `{ok, message, diagnosis, response}` — RCON allow-list only; rejected when RCON is disabled |

`lines` defaults to `CONSOLE_LOG_DEFAULT_LINES` (300) and is clamped to
`[1, CONSOLE_LOG_MAX_LINES]` (2000). Tails are read with a bounded `deque`, so a
huge log never loads into memory.

## UI

The Console section has three cards:

1. **Live status** — `detected_state` badge + reason, DB/actual status, PID,
   `process_exists`, and the `latest.log` / managed-log modification times.
2. **Logs** — a tab switch (Minecraft `latest.log` ↔ Managed process log), a
   monospace viewer, a line-count selector (100/300/1000), an **Auto-refresh
   (5s)** toggle and an **Auto-scroll** toggle, plus a manual **Refresh** button.
3. **RCON command** — only interactive when RCON is enabled; otherwise the box
   is shown disabled with a hint. Uses the same v0.2 allow-list; the password is
   never displayed.

The front-end (`static/js/main.js → initConsole`) polls `status` + the active
log every 5 s, but only while the tab is visible (`document.hidden`) and
auto-refresh is on. A `401`, or a redirect to `/login`, sends the user to the
login page; a `5xx`/unexpected response is shown inline rather than silently
swallowed.

## How to confirm a server is really running

1. Open the server's Console section.
2. Check **Live status**: `detected_state = running`, `process_exists = yes`, a
   non-empty PID, and a recent `latest.log` mtime.
3. Switch to **Managed process log** if `latest.log` is empty — a JVM-level
   failure (bad Java path, OOM at start) shows up there as stderr.
4. If RCON is enabled, run `list` from the command box for a live round-trip.

## Known limitations

- **Detection is heuristic.** The markers are substring matches; a custom
  server/mod that never prints `Done (`/`For help, type`/`Server thread/INFO`
  may sit at `starting`, and a healthy server that logs a stray `ERROR` during
  start could read as `crashed` until the completion line appears.
- **No WebSocket streaming.** Logs are polled (≈5 s), not streamed; this keeps
  the stack dependency-free (no ASGI websocket plumbing).
- **Managed log filename collisions.** Two servers whose names sanitise to the
  same string would share a managed-log file. Names are unique in the DB and
  almost always safe, but extreme names can collide.
- **No log rotation/pruning** from mc-appliance — managed logs are appended to
  (Minecraft rotates its own `latest.log`). Deletion stays out of scope under
  the v0.1 "no file deletion" rule.
- **Only `latest.log`** is exposed, not rolled/gzipped historical logs.
