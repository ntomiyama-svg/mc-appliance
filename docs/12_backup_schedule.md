# 12 · Backup Schedule (v0.3)

v0.3 adds **automatic, scheduled backups** on top of the existing manual zip
backups. Each server can have one schedule, managed from its detail page
(**Backup schedule** section).

## Model

A new table `backup_schedules`:

| Column            | Type      | Meaning |
|-------------------|-----------|---------|
| `id`              | int PK    | |
| `server_id`       | FK servers| owning server |
| `enabled`         | bool      | scheduler ignores disabled rows |
| `schedule_type`   | str       | `daily` or `interval` |
| `time_of_day`     | str `HH:MM` | for `daily` (local 24h time) |
| `interval_minutes`| int       | for `interval` |
| `retention_count` | int       | how many backups to keep (see note) |
| `last_run_at`     | datetime  | bookkeeping (when it last ran) |
| `last_status`     | str       | last run's result message |
| `created_at` / `updated_at` | datetime | |

The table is created automatically on startup by `create_all`. No manual
migration is needed.

## Scheduler

Implemented in
[`app/services/scheduler_service.py`](../app/services/scheduler_service.py) as a
single **daemon thread** started from the FastAPI startup hook. We deliberately
avoid APScheduler so v0.3 adds **no new dependency**.

- The thread wakes every `SCHEDULER_TICK_SECONDS` (default 60s).
- Each tick re-reads enabled schedules from the DB — so edits in the UI take
  effect on the next pass; there is nothing to "reload".
- **Due logic:**
  - `interval`: due if `last_run_at` is null or `now - last_run_at ≥ interval`.
  - `daily`: due if `now ≥ today@time_of_day` and we have not already run
    at/after that instant today.
- For a due schedule it creates a zip backup via the existing
  `backup_service.create_backup` (logs/backups excluded). If the server is up and
  RCON is enabled, it first runs `save-all flush` (best-effort) for a consistent
  snapshot — mirroring the manual backup path.
- The outcome is written to `BackupSchedule.last_run_at` / `last_status` and to
  `<LOG_DIR>/backup_scheduler.log`.

### Robustness

Every layer is wrapped so a failure can never take the app down:

- the thread is a **daemon** and its loop catches everything;
- each schedule runs in its own try/except — one failing server does not stop the
  others;
- `start_scheduler()` itself swallows startup errors (a broken scheduler must not
  stop the web app from serving).

## Retention — important note

`retention_count` is **stored and displayed**, but v0.3 does **not** prune old
backups. Deleting backup files would violate the v0.1 *"no file deletion"* rule
(see [`03_security_policy.md`](03_security_policy.md) and `CLAUDE.md`). Instead
the scheduler **logs** the current backup count and the configured retention each
run. Actual pruning is deferred until an explicit, audited delete path is added
(tracked in [`06_future_plan.md`](06_future_plan.md)).

## Using it

1. **Servers → (a server) → Backup schedule.**
2. Tick **Enable automatic backups**, choose **Daily** (set a time) or
   **Interval** (set minutes), set a retention count.
3. **Save schedule.** The dashboard's **Backup Schedules** card shows the number
   of enabled schedules.

## Without a real Minecraft server

Schedule creation/update, persistence, the dashboard count, the scheduler's
due-logic, and an actual scheduled zip backup are all verifiable against a
created (jar-less) server — the backup just zips whatever files exist. A running
server is only needed to exercise the optional pre-backup RCON `save-all`.
