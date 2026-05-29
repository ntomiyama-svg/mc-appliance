# 07 · Installation & systemd service (v0.1.1)

v0.1.1 makes mc-appliance installable as a long-running **systemd service**
without changing the v0.1 security model. The web app is still an *add-on* for
an existing Linux host, and it still **never runs as root**.

## Goals & non-goals

**In scope**

- Run the mc-appliance web app under systemd so it survives reboots and gets
  proper supervision (`Restart=on-failure`).
- A single installer that works on the Rocky/RHEL (`dnf`) and Debian/Ubuntu
  (`apt`) families, with **Rocky Linux 9 as the primary tested target**.
- A dedicated unprivileged service account and a clean separation of code,
  state, logs and config (FHS-friendly paths).

**Explicitly NOT in scope (still deferred to v0.2+)**

- Turning individual **Minecraft servers** into systemd units — they remain
  Python-managed subprocesses (see `docs/02_architecture.md`).
- Authentication, HTTPS, RCON, firewall configuration.
- Running the web app as root (forbidden by design and by the startup guard).

## On-disk layout (system install)

| Path | Owner | Purpose |
|------|-------|---------|
| `/opt/mc-appliance` | `root:mcapp` (read-only to service) | application code |
| `/opt/mc-appliance/.venv` | `root:mcapp` | Python virtualenv |
| `/var/lib/mc-appliance` | `mcapp:mcapp` | SQLite DB + data |
| `/var/lib/mc-appliance/backups` | `mcapp:mcapp` | generated zip backups |
| `/var/log/mc-appliance` | `mcapp:mcapp` | logs (incl. `server_logs/`) |
| `/etc/mc-appliance/config.yaml` | `root:mcapp` (mode 640) | configuration |
| `/etc/systemd/system/mc-appliance.service` | `root` | systemd unit |

Code being owned by root (not the service user) means the running web app
cannot rewrite its own code — a small defense-in-depth win.

## How paths are resolved (`app/config.py`)

`config.py` keeps the v0.1 behaviour for dev checkouts and layers system paths
on top. Precedence, highest first:

1. **Environment variable** — `MC_APPLIANCE_DATA_DIR`, `MC_APPLIANCE_LOG_DIR`,
   `MC_APPLIANCE_BACKUP_DIR`, `MC_APPLIANCE_HOST`, `MC_APPLIANCE_PORT`.
2. **Config file** — `/etc/mc-appliance/config.yaml` (or `$MC_APPLIANCE_CONFIG`).
3. **Built-in default** — project-root relative (`<repo>/data`, `<repo>/backups`).

PyYAML is an *optional* dependency: if it (or the config file) is missing, the
loader silently falls back to defaults, so a bare dev checkout needs nothing
new. The systemd unit sets `MC_APPLIANCE_CONFIG=/etc/mc-appliance/config.yaml`.

## Installer (`scripts/install.sh`)

Run as root: `sudo bash scripts/install.sh`. Steps:

1. **OS packages** — detects the package manager: on `dnf` it delegates to
   `scripts/install_rocky9.sh` (single source of truth for the Rocky package
   list); on `apt` it installs the Debian/Ubuntu equivalents inline.
2. **User** — creates the `mcapp` system user (no login shell) if absent.
3. **Deploy** — copies the app payload to `/opt/mc-appliance` via a `tar` pipe
   that excludes `.venv`, `.git`, `data/`, `backups/` and caches.
4. **Virtualenv** — builds `/opt/mc-appliance/.venv` and installs requirements.
5. **State/log dirs** — creates `/var/lib/mc-appliance`, its `backups/`, and
   `/var/log/mc-appliance`.
6. **Config** — writes `/etc/mc-appliance/config.yaml` if it does not already
   exist (an existing file is never clobbered).
7. **Ownership** — code root-owned/group-readable; state + logs owned by `mcapp`.
8. **systemd** — installs the unit and runs `systemctl daemon-reload`.

Useful environment overrides: `APP_USER`, `INSTALL_DIR`, `DATA_DIR`, `LOG_DIR`,
`CONFIG_DIR`, `SKIP_PKG=1` (skip step 1), `ENABLE_SERVICE=1` (enable + start at
the end). It is **idempotent enough** to re-run for upgrades: it reuses an
existing user and keeps an existing config.

## systemd unit (`scripts/mc-appliance.service`)

```ini
[Service]
User=mcapp
Group=mcapp
WorkingDirectory=/opt/mc-appliance
Environment=MC_APPLIANCE_CONFIG=/etc/mc-appliance/config.yaml
ExecStart=/opt/mc-appliance/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
Restart=on-failure
```

Light hardening is applied (`NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=full`,
`ReadWritePaths=` limited to the state + log dirs). Sandboxing is kept modest on
purpose: the app reads/writes *registered* server folders that may live anywhere
on the host, so a stricter `ProtectSystem=strict` is deferred to v0.2.

## Operating the service

```bash
sudo systemctl enable --now mc-appliance
systemctl status mc-appliance
sudo systemctl restart mc-appliance
journalctl -u mc-appliance -f
```

## Upgrading

Pull the new code, then re-run `sudo bash scripts/install.sh` (optionally with
`SKIP_PKG=1`) and `sudo systemctl restart mc-appliance`. The config file and the
DB under `/var/lib/mc-appliance` are preserved.
