# 02 · Architecture

## Stack

- **FastAPI** application served by **uvicorn**.
- **Jinja2** server-rendered templates + a small amount of **vanilla JS**
  (`static/js/main.js`) for live metric/log refresh. No SPA framework.
- **SQLite** database accessed via **SQLAlchemy** ORM.
- **psutil** for host metrics and process inspection.

## Component layout

```
Browser
  │  HTTP (LAN only in v0.1)
  ▼
uvicorn ── FastAPI (app/main.py)
  ├── routers/dashboard.py   GET /            , GET /api/metrics
  ├── routers/servers.py     /servers/*        (list, register, detail,
  │                                              start/stop/restart, properties,
  │                                              log/raw)
  ├── routers/backups.py     /backups, POST /backups/create
  └── routers/system.py      GET /system
        │ calls
        ▼
   services/
     server_detector.py    scan a folder → DetectionResult
     server_process.py     start/stop/restart, status reconciliation
     properties_service.py read/write server.properties (+ .bak)
     log_service.py        safe tail of logs/latest.log
     backup_service.py     zip backup confined to server_path
     system_metrics.py     psutil metrics + system info
        │ persists via
        ▼
   SQLAlchemy (database.py) → SQLite (data/mc_appliance.db)
```

## Request flow examples

### Start a server
1. `POST /servers/{id}/start`
2. `server_process.start_server()` reconciles status; if stopped, validates the
   path and jar, then `subprocess.Popen(..., start_new_session=True)`.
3. Child stdout/stderr is appended to `data/server_logs/server_{id}.log`.
4. PID + status are written to the DB; user is redirected back to the detail
   page with a status message.

### Status reconciliation
`refresh_status()` is called on dashboard, list and detail loads. It checks the
stored PID with psutil, verifies the process cwd matches `server_path` (to
defend against recycled PIDs), and updates the DB to `running`/`stopped`.

## Data model

- `servers` — one row per registered server (path, jar, java, memory, pid,
  status, detected type/version, mods/plugins flags, timestamps).
- `backups` — one row per created backup (server_id, name, path, size, time).

See `app/models.py` for the authoritative definition.

## Why subprocess (not systemd) in v0.1

v0.1 deliberately avoids privileged operations. Running servers as the app's
own user via `subprocess` keeps the security surface minimal and requires no
sudo/systemctl. The trade-off — process supervision tied to the app user and no
auto-restart on host reboot — is accepted for the MVP and addressed in v0.2 via
systemd unit generation.
