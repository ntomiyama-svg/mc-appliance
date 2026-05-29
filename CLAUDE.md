# CLAUDE.md — guidance for working in this repository

## What this project is

**mc-appliance** is a NAS-style web GUI for managing Minecraft servers on
Linux. It is an *add-on* app for an existing Linux host (not a dedicated OS),
with a future goal of a Rocky Linux 9 appliance image.

- Backend: **FastAPI** (Python), served by **uvicorn**
- Frontend: **Jinja2 + HTML/CSS/vanilla JS** (no SPA framework)
- DB: **SQLite** via **SQLAlchemy**
- Metrics: **psutil**
- Target Python: **3.9+** (use `typing.Optional`, not `X | None`, at runtime)

## Hard rules (v0.1 security model)

These are intentional constraints. Do **not** add features that violate them
without an explicit version bump and discussion:

- The web app must **never run as root** (startup aborts if euid == 0).
- **No sudo**, **no systemctl**, **no Docker/containers**.
- **No arbitrary command execution** exposed to users. The v0.2 RCON console is
  an **allow-list only** (see `services/rcon_service.ALLOWED_COMMANDS`); do not
  add a free-form command field without an explicit version bump + discussion.
- **No file deletion** and **no server deletion**.
- Only the `server.properties` of a **registered** server may be edited.
- All filesystem access must stay **inside the registered `server_path`**
  (guard against path traversal — see `log_service`, `properties_service`,
  `backup_service`).
- Stopping (v0.2) prefers a graceful **RCON** `save-all` + `stop`, and **falls
  back to SIGTERM**; **no automatic SIGKILL**. RCON features are admin-only and
  the RCON password is never logged or shown in plaintext.

## Architecture map

```
app/
  main.py            FastAPI app, router wiring, root-user guard
  config.py          paths + constants (DATA_DIR, BACKUP_DIR, timeouts)
  database.py        engine / SessionLocal / Base / get_db dependency
  models.py          Server, Backup ORM models + status constants
  schemas.py         Pydantic schemas
  templating.py      shared Jinja2Templates + filters/globals
  routers/           dashboard, servers (+properties), backups, system
  services/          server_detector, server_process, properties_service,
                     log_service, backup_service, system_metrics, rcon_service
  templates/         Jinja2 pages (base.html is the layout)
  static/            css/style.css, js/main.js
data/                SQLite DB + managed server stdout logs (gitignored)
backups/             generated zip backups (gitignored)
docs/                design docs
scripts/             dev_run.sh, install_rocky9.sh
```

## Process model (v0.1 launch, v0.2 stop)

Servers are launched with `subprocess.Popen(..., start_new_session=True)` from
`services/server_process.py`. The PID is stored on the `Server` row.
`refresh_status()` reconciles DB status with reality (using psutil and a cwd
check to avoid acting on a recycled PID). Launch is still subprocess-based
(per-server systemd is deferred). **Stop** is v0.2 RCON-first with a SIGTERM
fallback (`stop_server` → `_stop_via_rcon` / `_stop_via_sigterm`).

RCON uses the `rcon` PyPI package (not `mcrcon`, whose SIGALRM-based timeout
breaks in FastAPI's worker threadpool). See `docs/09_rcon.md`.

## Running locally

```bash
bash scripts/dev_run.sh        # venv + deps + uvicorn --reload on :8080
```

## Conventions

- Keep routers thin; put logic in `services/`.
- Every filesystem-touching service must validate paths against the registered
  `server_path` before reading/writing.
- Match the existing code style (type hints, docstrings, comments explaining
  *why*).
- Do not commit `data/`, `backups/`, or `.venv/`.
- Do not run `git commit` unless explicitly asked.
