# 09 · RCON integration (v0.2)

mc-appliance v0.2 can talk to a Minecraft server over **RCON** to send a small
set of safe console commands, flush the world to disk, and stop the server
gracefully. This document explains what RCON is, how to configure it, how
passwords are handled, which commands are allowed, and the safe-stop flow.

## What is RCON?

RCON (Remote Console) is a simple TCP protocol built into the vanilla Minecraft
server (and Paper/Spigot/Fabric, etc.). When enabled, the server listens on a
dedicated port and accepts a password-authenticated connection over which a
client can run console commands and read their output — the same commands you
would type into the server console.

It is the standard, supported way to control a running server *from another
process* without sharing the server's stdin. mc-appliance uses it so that
"Stop" can mean a clean in-game `save-all` + `stop` rather than a signal sent to
the OS process.

> ⚠️ **RCON has no transport encryption and a single shared password.** Treat
> the RCON port as a trusted-LAN/loopback-only service — see *Known limitations*.

### Library choice

We depend on the [`rcon`](https://pypi.org/project/rcon/) package (pinned in
`requirements.txt`). It was chosen over the equally popular `mcrcon` for one
concrete reason: `mcrcon` implements its connection timeout with
`signal.SIGALRM`, which only works on the **main thread**. FastAPI runs our
synchronous route handlers (and the stop path) in a worker threadpool, where
`signal.signal` raises `ValueError: signal only works in main thread`. The
`rcon` package uses plain socket timeouts instead, so it is safe to call from
any thread. It is also small, dependency-free, synchronous and actively
maintained.

The dependency is imported lazily inside `app/services/rcon_service.py`; if it
is ever missing, RCON features fail with a clear "library not installed"
message instead of breaking the rest of the app.

## Required `server.properties` settings

RCON is configured by three keys in the server's `server.properties`:

```properties
enable-rcon=true
rcon.port=25575
rcon.password=<a strong secret>
```

- `enable-rcon` must be `true`.
- `rcon.port` is the TCP port the server listens on (default `25575`).
- `rcon.password` must be **non-empty** — the server will not enable RCON
  without it.

**Changing any of these requires restarting the Minecraft server**; the values
are only read at startup. The UI reminds you of this after saving.

You can edit these from **Server detail → RCON → RCON settings**. Saving:

1. mirrors the values into the app database (so the app knows how to connect),
2. writes `enable-rcon` / `rcon.port` / `rcon.password` into `server.properties`
   (a `.bak` copy is made first, as with the normal properties editor), and
3. shows a "restart required" reminder.

The app stores a separate **connect host** (default `127.0.0.1`) used only to
reach the server from the machine running mc-appliance. In the normal
single-host setup this stays as loopback.

## Password management

- The RCON password is stored in two places that the settings form keeps in
  sync: `rcon.password` in `server.properties` and a mirror column in the app
  database.
- **The password is never displayed after it is saved.** The settings field is
  always rendered empty; leaving it blank on save keeps the current password.
- **The password is never written to logs**, never placed in a redirect URL,
  and never returned to the browser except by the explicit *Generate* action.
- **Generate** (`POST /servers/{id}/rcon/generate-password`) returns a fresh
  random alphanumeric password to the form *without persisting it*. You review
  it and then click *Save RCON settings* to store it. This keeps generated
  secrets out of server logs and browser history.

## Supported commands (v0.2)

The in-page console only accepts an **allow-list**; arbitrary command input is
intentionally not offered in v0.2. Allowed commands:

| Command | Notes |
| --- | --- |
| `list` | Online players |
| `say <message>` | Broadcast; message is sanitised (control chars stripped, ≤200 chars) |
| `save-all` | Flush world to disk |
| `save-all flush` | Flush and block until complete (preferred) |
| `whitelist list` | Show whitelist |
| `whitelist add <player>` | Player name must match `[A-Za-z0-9_]{1,16}` |
| `whitelist remove <player>` | Same validation |
| `op <player>` | Same validation |
| `deop <player>` | Same validation |
| `kick <player> <reason>` | Player validated; reason sanitised |
| `stop` | Graceful shutdown (confirmation prompt in the UI) |

All validation happens in `rcon_service.validate_command()`, the single
chokepoint every command goes through. Anything not on the list is rejected
before a connection is opened.

> **Future:** the allow-list is the seam for a later "advanced mode" that could
> permit free-form commands behind an explicit, audited opt-in. v0.2 keeps it
> closed.

## RCON connection test

**Server detail → RCON status → RCON Test** opens a connection and runs `list`.
On failure it reports a likely cause:

- **wrong password** → `enable-rcon`/`rcon.password` mismatch (auth rejected),
- **connection refused** → server stopped, `enable-rcon=false`, or wrong
  `rcon.port`,
- **timeout** → wrong port, a firewall in between, or the server still starting,
- **other connection error** → bad host / unreachable network.

The most recent result is also stored as the server's "Last status".

## Safe stop flow

When RCON is enabled, **Stop** prefers a graceful RCON shutdown
(`app/services/server_process.py`):

1. If RCON is enabled and reachable:
   1. send `save-all flush` over RCON,
   2. send `stop` over RCON,
   3. wait up to `RCON_STOP_TIMEOUT_SECONDS` (30s) for the process to exit,
   4. if it exits → mark **stopped**.
2. If RCON is disabled, cannot be reached, the RCON stop could not be issued, or
   the process is still alive after the wait → **fall back to SIGTERM** (the
   v0.1 path, up to `STOP_TIMEOUT_SECONDS` = 10s).
3. **SIGKILL is never sent automatically** (unchanged from v0.1).

A connection that drops *while issuing `stop`* is expected (the server closes
the socket as it exits) and is treated as success.

## Backups

Before creating a backup, if RCON is enabled and the server is running, the app
runs `save-all flush` so the zip captures a fully-saved world.

- If the save succeeds, the backup proceeds normally.
- If it fails, the default (v0.2) behaviour is **warn and continue** — the
  backup is still created and the warning is shown in the status message. The
  create-backup endpoint accepts `on_rcon_fail=abort` to instead skip the
  backup, leaving the design open for a future per-backup choice in the UI.

## Database changes

The `servers` table gained five columns (added to existing databases at startup
by a lightweight `ALTER TABLE` migration in `app/database.py` — Alembic is not
used yet):

| Column | Type | Default |
| --- | --- | --- |
| `rcon_enabled` | boolean | `false` |
| `rcon_host` | string | `127.0.0.1` |
| `rcon_port` | integer | `25575` |
| `rcon_password` | string | null |
| `rcon_last_status` | string | null |

## API

All endpoints require an authenticated admin session (enforced globally by
`AuthMiddleware`); unauthenticated callers are redirected to `/login`.

| Method & path | Purpose | Returns |
| --- | --- | --- |
| `POST /servers/{id}/rcon/settings` | Save enable/host/port/password (writes `server.properties`) | redirect + status |
| `POST /servers/{id}/rcon/generate-password` | Generate a password (not persisted) | JSON `{ok, password}` |
| `POST /servers/{id}/rcon/test` | Connect + run `list` | JSON `{ok, message, response, diagnosis}` |
| `POST /servers/{id}/rcon/command` | Run one allow-listed command | JSON `{ok, message, response, diagnosis}` |

## Known limitations

- **No transport security.** RCON traffic and the password are unencrypted.
  **Do not expose the RCON port to the public internet** — keep it on loopback
  or a trusted LAN, and firewall it. (The same applies to the web UI, which has
  no HTTPS yet.)
- **No arbitrary commands.** Only the allow-list above; advanced/free-form
  input is deferred.
- **Single shared password** per server, stored so the app can reconnect.
- **Settings changes need a server restart** to take effect.
- **`save-all flush` is best-effort** for backups; a failure does not block the
  backup by default.
- RCON replaces the *preferred* stop mechanism only; the SIGTERM fallback and
  the "no automatic SIGKILL" rule are unchanged.
