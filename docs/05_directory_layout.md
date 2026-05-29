# 05 · Directory Layout

```
mc-appliance/
├── app/
│   ├── main.py                 FastAPI app, router wiring, root-user guard
│   ├── config.py               paths + constants
│   ├── database.py             engine, SessionLocal, Base, get_db
│   ├── models.py               Server, Backup ORM models
│   ├── schemas.py              Pydantic schemas
│   ├── templating.py           shared Jinja2Templates + filters/globals
│   ├── routers/
│   │   ├── dashboard.py        /  and  /api/metrics
│   │   ├── servers.py          /servers/*  (+ properties, log/raw)
│   │   ├── backups.py          /backups, /backups/create
│   │   └── system.py           /system
│   ├── services/
│   │   ├── server_detector.py  folder scan → DetectionResult
│   │   ├── server_process.py   start/stop/restart + status
│   │   ├── properties_service.py  read/write server.properties (+.bak)
│   │   ├── log_service.py      safe latest.log tail
│   │   ├── backup_service.py   zip backup confined to server_path
│   │   └── system_metrics.py   psutil metrics + system info
│   ├── templates/
│   │   ├── base.html           layout (topbar + sidebar)
│   │   ├── dashboard.html
│   │   ├── servers.html
│   │   ├── server_detail.html
│   │   ├── server_register.html
│   │   ├── properties.html
│   │   ├── backups.html
│   │   ├── system.html
│   │   └── error.html
│   └── static/
│       ├── css/style.css
│       └── js/main.js
├── data/                       SQLite DB + managed server stdout logs (gitignored)
│   ├── mc_appliance.db
│   └── server_logs/server_<id>.log
├── backups/                    generated zip backups (gitignored)
│   └── <server_name>/<timestamp>.zip
├── docs/                       this documentation
├── scripts/
│   ├── dev_run.sh              venv + deps + uvicorn --reload
│   └── install_rocky9.sh       Rocky 9 dev package install (non-destructive)
├── requirements.txt
├── README.md
├── CLAUDE.md
└── .gitignore
```

## Notes

- `data/` and `backups/` are tracked only via a `.gitkeep`; their contents are
  ignored by git.
- `templating.py` and `system.html`/`error.html` are additions beyond the
  original skeleton: a shared templates instance keeps Jinja filters/globals in
  one place, and the System page is its own router as specified.
- Managed stdout/stderr of launched servers goes to
  `data/server_logs/server_<id>.log`; the server's own `logs/latest.log` is
  read separately for the UI tail.
