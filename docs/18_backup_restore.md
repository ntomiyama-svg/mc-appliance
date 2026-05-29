# 18. Backup / Restore hardening (v0.6)

v0.6 adds a hardened backup and restore path on top of the v0.3 zip backups:
**rsync hardlink snapshots** taken with the server **stopped**, automatic
**generation retention**, an optional **backup-on-start**, and a guarded
**restore**. It is designed so a backup can never corrupt a world and a restore
can never lose the current state without a safety net.

> The v0.3 "Create backup" (zip, while-running) button still works for quick
> snapshots. The v0.6 **Backups** section on the server detail page is the
> hardened path described here.

## Why stop the server before backing up

A running Minecraft server holds world data in memory and writes region files
continuously. Copying the world directory while the server is running can
capture a **torn, half-written** region file, producing a backup that looks fine
but is subtly corrupt. v0.6 therefore **always stops the server first** for a
consistent on-disk state, then takes the snapshot, then restarts it — but only
if it had been running.

The stop uses the existing **safe stop** (`server_process.stop_server`): RCON
`save-all` + `stop` → write `stop` to the console stdin → `SIGTERM`. **`SIGKILL`
is never used in the backup path.** If a running server cannot be stopped, the
backup is **aborted** and nothing is started or killed — the server is left as it
was for the operator to resolve.

State handling rules:

- The pre-backup run state is recorded.
- A running server is stopped and confirmed stopped before the snapshot.
- A server that does not stop ⇒ **backup aborted** (no snapshot, no SIGKILL).
- After the snapshot, the server is restarted **only if it was running** before.
- A server that was already **stopped stays stopped** — it is never started by a
  backup.

## rsync hardlink snapshots

Each backup is a full, browsable copy of the server directory under:

```
<BACKUP_DIR>/<server_name>/<timestamp>/
```

(`<BACKUP_DIR>` is `/var/lib/mc-appliance/backups` on a system install,
`./backups` in a dev checkout; `<timestamp>` is `YYYYMMDD_HHMMSS`.)

- The **first** backup is a full copy.
- **Subsequent** backups pass the previous successful snapshot to
  `rsync -a --delete --link-dest <previous>`. Files that have not changed since
  the previous generation are stored as **hardlinks** to it rather than copied
  again. The result is space-efficient (unchanged files cost one directory
  entry, not a second copy) while every generation remains a **complete,
  independently restorable tree** — deleting one generation never harms another.

Backup metadata is stored in the `backups` table: `method` (`rsync`/`zip`),
`backup_type` (`normal`/`pre_restore`), `status` (`in_progress`/`ok`/`failed`),
`size_bytes`, `note`, `created_at`, and the snapshot path. `status` moves
`in_progress → ok` (or `→ failed`) so a crash mid-snapshot is visible, never
silently treated as a good backup.

`rsync` is required for this path. It is installed by `scripts/install_rocky9.sh`
and `scripts/install.sh`. If `rsync` is missing the backup fails cleanly with an
explanatory message (the server is still restarted if it had been running).

## Backup-on-start

When **Back up before Start** is enabled for a server, clicking **Start** takes a
snapshot *before* launching:

- If the last successful snapshot is **newer** than
  `backup_on_start_min_interval_hours`, the backup is **skipped** and the reason
  is written to the managed log (so frequent restarts don't pile up snapshots).
- Otherwise a snapshot is taken. If it **fails**, the **Start is aborted** and the
  UI shows **"backup failed, start aborted"** — the server is not launched.

Notes / cautions:

- Backup-on-start runs only for the manual **Start** action, not for **Restart**.
- If the server is already running, the hook is a no-op (nothing to snapshot
  consistently for an already-up server).

## Daily scheduled backups

Per-server settings:

| Setting | Default | Meaning |
|---|---|---|
| `backup_enabled` | `false` | Enable the daily snapshot |
| `backup_time` | `04:00` | Daily time, **host-local** `HH:MM` |
| `backup_on_start_enabled` | `true` | Snapshot before a manual Start |
| `backup_on_start_min_interval_hours` | `6` | Skip on-start backup if backed up within this window |
| `backup_keep_generations` | `7` | Successful normal snapshots to retain |

The in-process scheduler (`scheduler_service`, the same daemon thread used by the
v0.3 `BackupSchedule`) checks each enabled server every tick and runs the daily
snapshot when the host-local clock matches `backup_time`. A successful normal
snapshot within the last 12 hours suppresses a duplicate run in the same minute
or right after an app restart. Daily backups use the same stop-first flow.

## Generation management

- Retention applies to **successful, normal, rsync** snapshots only.
- After each successful normal backup, the newest `backup_keep_generations`
  (default **7**) are kept; older normal snapshots are pruned (directory + DB
  row removed). Creating the 8th generation prunes the oldest normal one.
- **Pre-restore** snapshots are a distinct type and are **never** pruned by
  retention — they are your safety net for restores.
- `failed` / `in_progress` backups are left in place (and visible) for
  inspection; they are not counted toward the kept generations.

> **File deletion scope.** Retention pruning and the **Delete** button are the
> only places this feature removes files, and they are **strictly limited to
> backup snapshots under `BACKUP_DIR`** (the path is validated before any
> removal). Registered **server files are never deleted** by a backup. This is a
> deliberate, v0.6-scoped exception to the v0.1 "no file deletion" rule, made
> only for managing backups.

## Restoring

1. **Stop the server.** Restore is **refused while the server is running**.
2. From the server's **Backups** table, click **Restore** on a completed rsync
   snapshot (Restore is disabled for running servers, failed backups, and zip
   backups).
3. A **pre-restore safety snapshot** of the current state is taken first. If that
   snapshot fails, the restore is **aborted** (your current state is untouched).
4. The selected snapshot is copied back over the server directory with
   `rsync -a --delete`, making it a faithful revert to that generation.
5. The server is **left stopped** — it is **not** auto-started. Start it yourself
   when you are happy with the result.

The restore result (and the pre-restore snapshot) are recorded in the database
and the managed log.

## Where things are recorded

- **Managed log** (Console → managed log): every stop/snapshot/restart, skip
  reason, retention prune, restore and delete is appended here.
- **Database**: each backup row (type/status/size/note) and the pre-restore
  snapshot created by a restore.
- **GUI**: the **Backups** section lists every backup with created time, type,
  size, state, note, and Restore/Delete actions; flash messages report each
  action's outcome (including "backup failed, start aborted").

## Security / safety summary

- The web app still **never runs as root**; backups run as the `mcapp` user.
- **No SIGKILL** in the backup path; stops use the existing safe-stop ladder.
- All snapshot writes/reads and restore sources stay **inside `BACKUP_DIR`**;
  the restore target is the **registered `server_path`**. Paths are validated to
  prevent traversal.
- Deletion is confined to backup snapshots under `BACKUP_DIR`.
