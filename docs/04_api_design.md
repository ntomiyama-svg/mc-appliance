# 04 · API / Route Design

v0.1 is primarily server-rendered (HTML responses with form posts and 303
redirects). A small number of lightweight JSON/text endpoints support live UI
refresh.

## Pages (HTML)

| Method | Path                          | Description                              |
|--------|-------------------------------|------------------------------------------|
| GET    | `/`                           | Dashboard (counts + CPU/mem/disk)        |
| GET    | `/servers`                    | Server list with actions                 |
| GET    | `/servers/register`           | Registration form (`?path=` to scan)     |
| POST   | `/servers/register`           | Create a server record                   |
| GET    | `/servers/{id}`               | Server detail (info, log, command)       |
| GET    | `/servers/{id}/properties`    | Edit `server.properties`                 |
| POST   | `/servers/{id}/properties`    | Save `server.properties` (+ `.bak`)      |
| GET    | `/system`                     | Host system information                  |
| GET    | `/backups`                    | Backup history list                      |

## Actions (POST → 303 redirect with `?msg=`)

| Method | Path                       | Description                       |
|--------|----------------------------|-----------------------------------|
| POST   | `/servers/{id}/start`      | Start server (subprocess)         |
| POST   | `/servers/{id}/stop`       | Stop server (SIGTERM, 10s wait)   |
| POST   | `/servers/{id}/restart`    | Restart (stop then start)         |
| POST   | `/backups/create`          | Create a zip backup               |

## Lightweight data endpoints

| Method | Path                          | Returns                            |
|--------|-------------------------------|------------------------------------|
| GET    | `/api/metrics`                | JSON: cpu/mem/disk percentages     |
| GET    | `/servers/{id}/log/raw`       | text/plain: tail of latest.log     |

## Form fields

### `POST /servers/register`
- `name` (required)
- `server_path` (required, must be an existing directory)
- `java_path` (default `/usr/bin/java`)
- `min_memory` (default `1G`)
- `max_memory` (default `4G`)
- `jar_file` (must be a jar detected in `server_path`)

### `POST /servers/{id}/properties`
- `prop__<key>` per existing property key. Only existing keys are saved.

### `POST /backups/create`
- `server_id` (required)
- `exclude_logs` (bool, default in UI: true)
- `exclude_backups` (bool, default in UI: true)

## Conventions

- Mutations use POST + 303 redirect (Post/Redirect/Get) to avoid resubmission.
- Status messages are passed via a `msg` query parameter and shown as a flash.
- v0.2 may add a versioned JSON API (`/api/v1/...`) once auth exists.
