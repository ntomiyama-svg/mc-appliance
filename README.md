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
- View and edit `server.properties` (with automatic `.bak` backup)
- Create manual zip backups
- See host system info (OS, Python, Java, disk, IP)

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

## Registering an existing Minecraft server

1. Click **Servers → Register Server**.
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
- Stopping a server uses `SIGTERM` only; if it does not exit within 10 seconds
  the stop is reported as **failed** (no automatic `SIGKILL` in v0.1).
- Editing is restricted to the `server.properties` of a registered server.
- There is **no delete** function and **no arbitrary command execution**.

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
- No systemd integration
- No RCON (graceful in-game stop / commands)
- No sudo / privileged operations
- No `SIGKILL` / force-kill
- No backup restore
- No server deletion or file deletion
- No Ubuntu/Debian-specific tooling

## Roadmap / future plans

See [`docs/06_future_plan.md`](docs/06_future_plan.md). Highlights:

- v0.1.2: administrator login (single admin, cookie sessions) ✅
- v0.2: HTTPS, CSRF tokens, multi-user/roles, RCON-based safe stop, systemd unit management
- v0.3: backup restore, scheduling, multi-user roles
- Later: Rocky Linux appliance image, Ubuntu/Debian support

## Documentation

- [`docs/01_overview.md`](docs/01_overview.md)
- [`docs/02_architecture.md`](docs/02_architecture.md)
- [`docs/03_security_policy.md`](docs/03_security_policy.md)
- [`docs/04_api_design.md`](docs/04_api_design.md)
- [`docs/05_directory_layout.md`](docs/05_directory_layout.md)
- [`docs/06_future_plan.md`](docs/06_future_plan.md)
- [`docs/08_authentication.md`](docs/08_authentication.md)
