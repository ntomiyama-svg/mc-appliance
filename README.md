# mc-appliance

A lightweight, NAS-style **web GUI for managing Minecraft servers on Linux**.
Register existing server folders and start / stop / monitor them from your
browser — no desktop GUI, no containers, no root daemon.

> ⚠️ **v0.1.2 adds administrator login, but still has NO HTTPS.**
> Credentials and the session cookie travel in cleartext, so **do not expose
> this instance to the public internet.** Run it only on a trusted LAN or
> behind a VPN / TLS-terminating reverse proxy. HTTPS arrives in a later
> version. See [Authentication](#authentication) and
> [Security notes](#security-notes).

---

## Overview

mc-appliance is an *add-on* application for an existing Linux server. It is not
a dedicated OS image (though a Rocky Linux–based appliance is a future goal).
It lets you:

- View a dashboard (server counts, CPU / memory / disk usage)
- Register an existing Minecraft server folder
- Auto-detect server type, jars, world, mods, plugins, etc.
- Start / stop / restart servers (Python-managed subprocess, PID stored in DB)
- View server status and the tail of `logs/latest.log`
- **Server console (v0.5):** live status (PID, real process state, detected
  state), both `latest.log` and the mc-appliance-managed stdout/stderr log, and
  the allow-listed RCON command box — see [Server console](#server-console-v05)
- View and edit `server.properties` (with automatic `.bak` backup)
- Create manual zip backups
- See host system info (OS, Python, Java, disk, IP)
- **Java Runtime Manager (v0.6):** detect installed Java runtimes and, when
  opted in, install Java 8 / 11 / 17 / 21 / 25 from the GUI via a locked-down
  helper — see [Java Runtime Manager](#java-runtime-manager--java-installer-v06)

## Supported OS

- **Primary target:** Rocky Linux 9
- Should also work on other modern Linux distributions with Python 3.9+
- Ubuntu / Debian support is planned

## Rocky Linux 9 — required packages

```bash
sudo dnf install -y python3 python3-pip python3-virtualenv git zip unzip java-17-openjdk-headless
```

Or just run the helper (development preparation only, no destructive changes):

```bash
sudo bash scripts/install_rocky9.sh
```

## Python virtual environment

```bash
cd mc-appliance
python3 -m venv .venv
source .venv/bin/activate
```

## Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## Running

The easiest way (creates venv if missing, installs deps, starts with reload):

```bash
bash scripts/dev_run.sh
```

Or manually:

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

> The app **refuses to start as root** by design. Run it as a normal user that
> has read/write access to your Minecraft server folders.

### Configuration (dev vs prod)

Storage paths and the bind host/port come from an optional YAML config file.
The file is located in this order (highest precedence first):

1. **`$MC_APPLIANCE_CONFIG`** — explicit path. The systemd unit always sets this
   to `/etc/mc-appliance/config.yaml`.
2. **`./data/config.yaml`** — *dev only* (when `MC_APPLIANCE_CONFIG` is unset and
   the app detects a dev checkout). Lets a checkout keep its own config without
   touching `/etc`.
3. **`/etc/mc-appliance/config.yaml`** — the system-install default.

Individual `MC_APPLIANCE_*` environment variables (e.g. `MC_APPLIANCE_DATA_DIR`,
`MC_APPLIANCE_SERVERS_DIR`) still override whatever the file provides.

`scripts/dev_run.sh` exports `MC_APPLIANCE_ENV=dev` and leaves
`MC_APPLIANCE_CONFIG` unset, so a dev run prefers `./data/config.yaml`. If
`/etc/mc-appliance/config.yaml` exists but is **not readable** (e.g. root-owned
on a shared host), a dev run logs a warning and falls back to built-in defaults
instead of aborting. "dev vs prod" is decided by `MC_APPLIANCE_ENV`
(`dev`/`prod`), falling back to a heuristic (installs live under `/opt`).

## Installing as a system service (systemd)

For a persistent install that survives reboots, use the installer. It runs as
root only to set things up — the **web app itself runs as the unprivileged
`mcapp` user**, never as root.

```bash
sudo bash scripts/install.sh
```

This will:

- install OS packages (`dnf` on Rocky/RHEL, `apt` on Debian/Ubuntu)
- create the system user **`mcapp`**
- deploy the app to **`/opt/mc-appliance`** (owned by root, read-only to the service)
- build a virtualenv at **`/opt/mc-appliance/.venv`** and install requirements
- create **`/var/lib/mc-appliance`** (DB / data) and **`/var/log/mc-appliance`** (logs)
- write a default **`/etc/mc-appliance/config.yaml`** (left untouched if it exists)
- install the **`mc-appliance.service`** systemd unit

> Primary tested target is **Rocky Linux 9**. Debian/Ubuntu uses `apt` and is
> supported by design but less exercised. Useful flags:
> `SKIP_PKG=1` (skip OS packages), `ENABLE_SERVICE=1` (enable + start at the end).

Then manage it with systemctl:

```bash
sudo systemctl enable --now mc-appliance     # start now and on boot
systemctl status mc-appliance
sudo systemctl restart mc-appliance
sudo systemctl stop mc-appliance
journalctl -u mc-appliance -f                # follow service logs
```

The service binds `0.0.0.0:8080`. Edit `/etc/mc-appliance/config.yaml` to change
storage paths; the unit passes `--host`/`--port` to uvicorn explicitly (edit the
unit, or use a drop-in, to change the bind).

> **Scope (v0.1.1):** this manages *the mc-appliance web app* as a service only.
> Minecraft servers are still launched as managed subprocesses (see below) — they
> are **not** turned into systemd units yet. Auth, HTTPS and RCON are still absent.

## Accessing the web UI

Open a browser to:

```
http://<server-ip>:8080/
```

(Use `http://localhost:8080/` if you are on the same machine.)

## Authentication

Since **v0.1.2** the web UI requires an administrator login. All pages and all
change operations (start/stop/restart, register, save `server.properties`,
create backup) are gated; `/login` and `/static/*` are the only routes
reachable while signed out. Unauthenticated HTML requests redirect to `/login`;
unauthenticated `/api/*` requests get `401`.

v0.1.2 is intentionally a **single-admin** model — no signup, roles, or
user-management UI yet (see [`docs/08_authentication.md`](docs/08_authentication.md)).

### 1. Set the session secret key

Sessions are signed with a secret key read from the `MC_APPLIANCE_SECRET_KEY`
environment variable. There is a built-in fallback **for development only** —
it is public and insecure. **Set your own in any real deployment:**

```bash
export MC_APPLIANCE_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
```

For the systemd unit, add it as an `Environment=` / `EnvironmentFile=` entry so
it is set for the service user.

### 2. Create the initial administrator

Run the interactive helper as the same (non-root) user that runs the app:

```bash
source .venv/bin/activate
python scripts/create_admin.py
```

It prompts for a username and password (entered twice, hidden), hashes the
password with bcrypt, and stores it in the SQLite database. It refuses to
create a user whose name already exists. There is no UI to edit or delete
admins in v0.1.2, so create the account you intend to use.

### 3. Log in / log out

1. Open `http://<server-ip>:8080/` — you are redirected to `/login`.
2. Enter the username and password you created.
3. The signed-in username and a **Logout** link appear in the top bar. Logging
   out clears the session and returns you to the login screen.

## RCON (v0.2)

Since **v0.2** mc-appliance can talk to a running server over **RCON** to send a
small allow-listed set of console commands, flush the world (`save-all`), and
stop the server gracefully. Full details are in
[`docs/09_rcon.md`](docs/09_rcon.md).

### Enabling RCON

1. Open **Servers → (a server) → RCON → RCON settings**.
2. Tick **enable-rcon**, set `rcon.port` (default `25575`), and either type or
   **Generate** an `rcon.password`.
3. Click **Save RCON settings** — this writes `enable-rcon` / `rcon.port` /
   `rcon.password` into the server's `server.properties` (a `.bak` is created
   first) and mirrors the connection settings into the app database.
4. **Restart the Minecraft server** — RCON settings are only read at startup.
5. Use **RCON Test** to confirm the connection, then the quick-command buttons
   or the console input (allow-list only).

> ⚠️ **Do not expose the RCON port (e.g. 25575) to the public internet.** RCON
> traffic and its password are unencrypted. Keep the port on loopback
> (`127.0.0.1`) or a trusted LAN and firewall it off from the outside. The
> password is never displayed after saving and never written to logs.

When RCON is enabled, **Stop** uses RCON `save-all` + `stop` first and only
falls back to `SIGTERM` if RCON is unavailable or fails; backups run
`save-all flush` beforehand so the snapshot is consistent.

## Server console (v0.5)

Since **v0.5** each server has a **Console** section (on the server detail page,
and as a standalone `/servers/<id>/console` page) that shows — live, in the
browser — whether the server is *really* running. Full details:
[`docs/14_server_console.md`](docs/14_server_console.md).

It shows:

- **Live status** — a detected state (`stopped` / `starting` / `running` /
  `crashed` / `unknown`) derived from the real process *and* the logs, plus the
  PID, whether the process actually exists, and each log's last-modified time.
- **Two logs, switchable:**
  - **Minecraft `latest.log`** — the server's own log
    (`<server_path>/logs/latest.log`).
  - **Managed process log** — the stdout/stderr mc-appliance captured when *it*
    launched the server, under `MANAGED_LOG_DIR` (default
    `<DATA_DIR>/server_logs/<name>.log`; a system install points this at
    `/var/log/mc-appliance/server_logs/`). Set `MC_APPLIANCE_LOG_DIR=./logs` in
    dev for `./logs/server_logs/<name>.log`. This is where a JVM-level startup
    failure (bad Java path, OOM) shows up even when `latest.log` is empty.
- **Controls** — auto-refresh (~5 s, only while the tab is visible), auto-scroll,
  a manual refresh, and a 100/300/1000 line selector (capped at 2000).
- **RCON command box** — only when RCON is enabled, and only the **v0.2
  allow-list** (no free-form commands). The password is never displayed.

How to confirm a server is up: open the Console, check `detected_state =
running` with a PID and `process_exists = yes`, and (if RCON is on) run `list`.

> The console is **read-only** beyond the existing RCON allow-list. It reads only
> `<server_path>/logs/latest.log` and the managed log — no arbitrary file access
> — every endpoint requires login, and the RCON password is never logged or
> shown. The persisted server `status` is unchanged; the starting/crashed
> verdict is a separate, detection-only signal.

## Java Runtime Manager / Java installer (v0.6)

The **Java Runtimes** screen (sidebar, or **System → Java Runtime Manager**)
shows the Java runtimes installed on the host, the **recommended** Java, the
Java **required** by your registered servers (inferred from their Minecraft
version), and which supported versions are **not installed**. Supported major
versions are **8 / 11 / 17 / 21 / 25**. Full details:
[`docs/16_java_installer.md`](docs/16_java_installer.md).

When enabled, the screen offers **Install Java 8 / 11 / 17 / 21 / 25** buttons
(login required). The web app never installs packages itself: it shells out —
through `sudo` — to a single fixed helper, `scripts/install_java_runtime.sh`,
passing **only** the Java major version. No arbitrary commands, no arbitrary
package names.

> **GUI install is DISABLED by default.** The manager is read-only until an
> operator opts in. Re-run the system installer with `ENABLE_JAVA_INSTALLER=1`
> to deploy the helper and write a **minimal `sudoers`** rule (one NOPASSWD line
> per allowed version, no wildcards):
>
> ```bash
> sudo ENABLE_JAVA_INSTALLER=1 bash scripts/install.sh
> ```
>
> Until then the page shows *"GUI install is not enabled"* and still displays a
> **manual install command** for each version.

A few cautions:

- **Not every Java version is packaged on every distribution.** In particular
  **Java 25 may not be available** yet on your distro. An install that fails for
  a missing package is reported as an error with the package-manager output.
- The installer is **additive** — it never changes the system default `java`.
  Each server keeps selecting its own **`java_path`**.
- Install output is logged to `/var/log/mc-appliance/java_install.log`
  (`./logs/java_install.log` in a dev checkout) and shown in the GUI. No secrets
  are logged.

## Creating a new Minecraft server (v0.3)

Since **v0.3** you can create a brand-new server from the GUI — no shell needed.
Full details: [`docs/10_server_creation.md`](docs/10_server_creation.md).

1. Click **Servers → Create Server**.
2. Fill in the form: name (letters/digits/`-`/`_` only — it becomes the folder
   name), server type, port, memory, MOTD, max players, gamemode, difficulty,
   online-mode, optional RCON settings, and an optional `server.jar` upload
   (`.jar` only).
3. Tick the **Minecraft EULA** checkbox (required) and click **Create server**.

The app creates `SERVERS_DIR/<name>/` with `eula.txt` (`eula=true`),
`server.properties` (generated from your inputs), empty `mods/` and `plugins/`
directories, and stores the uploaded jar as `server.jar`. A `servers` row is
registered (`jar_file=server.jar`, `java_path=/usr/bin/java`, `status=stopped`,
RCON mirrored). `SERVERS_DIR` defaults to `./data/servers` in a dev checkout and
`/var/lib/mc-appliance/servers` under a system install.

> The new-server directory, files, jar upload, DB row, and mod/plugin scaffolding
> are all created **without a real Minecraft server**. Actually *starting* it
> still needs a runnable jar and working Java (unchanged from v0.1).

### Vanilla server jar cache (v0.4)

Since **v0.4** you can fetch the official **Vanilla** Minecraft Java Edition
`server.jar` from Mojang and cache it locally, then create a server from the
cached jar instead of uploading one. Full details:
[`docs/13_vanilla_jar_cache.md`](docs/13_vanilla_jar_cache.md).

1. Open **JAR Cache** in the sidebar (`/jars/vanilla`).
2. Click **Check latest** to see the current release, then **Download latest**
   to cache its `server.jar`. The table lists cached versions, sizes and
   download times.
3. On **Servers → Create Server**, set **server.jar source** to *Use cached
   latest Vanilla server.jar* or *Use cached Vanilla version*. The cached jar is
   copied to `<server>/server.jar`. (You still must accept the EULA.)

Cached jars live under `JAR_CACHE_DIR` — `./data/jars` in a dev checkout and
`/var/lib/mc-appliance/jars` under a system install (one folder per version).

> **Vanilla only.** Paper/Fabric/Forge/NeoForge still require a jar upload. This
> is **separate** from RCON and from running servers, and it needs **outbound
> internet** to Mojang. Only the `server.jar` URL resolved *through* the Mojang
> manifest is ever downloaded — there is **no arbitrary-URL download**. SHA-1 is
> verified when Mojang provides it. In **offline** environments, use the upload
> path; the dashboard warning/checks degrade silently and never crash the app.

### Mods & Plugins (v0.3)

Each server's detail page has **Mods** and **Plugins** sections: upload `.jar`
files and enable/disable them (disabling renames `foo.jar` → `foo.jar.disabled`).
There is **no delete**. See
[`docs/11_mod_plugin_management.md`](docs/11_mod_plugin_management.md).

### Scheduled backups (v0.3)

Each server's detail page has a **Backup schedule** section: enable daily (at a
time) or interval (every N minutes) automatic backups. An in-process daemon
thread runs them while the app is up and logs outcomes to
`backup_scheduler.log`; the dashboard shows the number of enabled schedules.

> **Retention is recorded but old backups are not pruned in v0.3** — file
> deletion is intentionally out of scope (see the security model). See
> [`docs/12_backup_schedule.md`](docs/12_backup_schedule.md).

## Registering an existing Minecraft server

1. Click **Servers → Register existing folder**.
2. Enter the absolute path to your existing server folder (e.g.
   `/srv/minecraft/survival`) and click **Scan folder**.
3. Review the detection results, give the server a display name, pick the jar
   (defaults to the first/detected candidate), confirm Java path and memory,
   then click **Register server**.

The app records the path and detected metadata in a local SQLite database; it
never copies or moves your existing data during registration.

## Security notes

- **Login is required (v0.1.2)** but **HTTPS is not implemented.** Credentials
  and the session cookie travel in cleartext — keep the app on a trusted LAN or
  behind a VPN / TLS-terminating reverse proxy. Do **not** expose it to the
  public internet.
- **Set `MC_APPLIANCE_SECRET_KEY`** to a long random value in any real
  deployment. The built-in fallback is public and lets anyone forge admin
  sessions. See [Authentication](#authentication).
- Passwords are stored only as **bcrypt hashes** (passlib); plaintext is never
  written to disk.
- CSRF protection in v0.1.2 is lightweight (session cookie uses
  `SameSite=Lax`). A full CSRF-token scheme is planned — see
  [`docs/08_authentication.md`](docs/08_authentication.md).
- The app still **refuses to run as root** and exposes no privileged
  operations.

## Notes / cautions

- The app runs Minecraft servers as **its own user**, via `subprocess`.
- Stopping a server prefers a graceful **RCON** `save-all` + `stop` when RCON is
  enabled, and **falls back to `SIGTERM`** otherwise (or if RCON fails). There
  is still **no automatic `SIGKILL`**. See [RCON](#rcon-v02).
- Editing is restricted to the `server.properties` of a registered server.
- The RCON console only allows a fixed set of commands; there is **no arbitrary
  command execution**.
- There is **no delete** function.

## What v0.1 can do

- Dashboard with live CPU / memory / disk metrics
- Register existing server folders + auto-detection
- Start / stop / restart via managed subprocess (PID stored in DB)
- Status reconciliation against the real process
- `logs/latest.log` tail (last 200 lines, auto-refresh)
- `server.properties` view + edit (with `.bak`)
- Manual zip backups (logs/backups excluded) + history
- System info page

## What v0.1 cannot do (yet)

- No HTTPS (login/session travel in cleartext)
- No multi-user accounts, roles, or user-management UI (single admin only)
- No CSRF tokens (only `SameSite=Lax` cookie mitigation)
- No **per-server** systemd integration (the app *itself* can run as a systemd
  service — see "Installing as a system service" — but Minecraft servers are
  still managed as subprocesses)
- No sudo / privileged operations
- No `SIGKILL` / force-kill (RCON `stop` / `SIGTERM` only)
- No arbitrary RCON commands (allow-list only — see [RCON](#rcon-v02))
- No backup restore
- No server deletion or file deletion
- No Ubuntu/Debian-specific tooling

## Roadmap / future plans

See [`docs/06_future_plan.md`](docs/06_future_plan.md). Highlights:

- v0.1.2: administrator login (single admin, cookie sessions) ✅
- v0.2: RCON-based safe stop + allow-listed console + `save-all` before backups ✅
- v0.3: GUI server creation, mod/plugin management, scheduled backups ✅
- v0.4: Vanilla server.jar cache (Mojang manifest fetch + create-from-cache) ✅
- v0.2+ (pending): HTTPS, CSRF tokens, multi-user/roles, systemd unit management
- v0.3+ (pending): backup restore, backup retention pruning, multi-user roles
- Later: Rocky Linux appliance image, Ubuntu/Debian support

## Documentation

- [`docs/01_overview.md`](docs/01_overview.md)
- [`docs/02_architecture.md`](docs/02_architecture.md)
- [`docs/03_security_policy.md`](docs/03_security_policy.md)
- [`docs/04_api_design.md`](docs/04_api_design.md)
- [`docs/05_directory_layout.md`](docs/05_directory_layout.md)
- [`docs/06_future_plan.md`](docs/06_future_plan.md)
- [`docs/08_authentication.md`](docs/08_authentication.md)
- [`docs/09_rcon.md`](docs/09_rcon.md)
- [`docs/10_server_creation.md`](docs/10_server_creation.md)
- [`docs/11_mod_plugin_management.md`](docs/11_mod_plugin_management.md)
- [`docs/12_backup_schedule.md`](docs/12_backup_schedule.md)
- [`docs/13_vanilla_jar_cache.md`](docs/13_vanilla_jar_cache.md)
