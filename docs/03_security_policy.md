# 03 · Security Policy

## v0.1 threat posture

mc-appliance v0.1 has **no authentication**. It is intended to run on a trusted
LAN or behind a VPN, operated by a single trusted administrator. It must not be
exposed to the public internet.

## Enforced constraints

1. **Never run as root.** The app aborts on startup if `os.geteuid() == 0`.
   `scripts/dev_run.sh` also refuses to launch as root.
2. **No sudo, no systemctl, no containers** are used anywhere in v0.1.
3. **No arbitrary command execution** is exposed. The only process the app
   launches is `java -jar` for a registered server, built from validated fields.
4. **Path confinement.** Every filesystem operation is restricted to a
   registered server's `server_path`:
   - `log_service` resolves `logs/latest.log` and rejects paths that escape.
   - `properties_service` only opens `<server_path>/server.properties`.
   - `backup_service` only archives files whose resolved path stays within
     `server_path`, and skips symlinks pointing outside it.
   - `jar_file` must be one of the jars detected directly inside `server_path`.
5. **No delete.** There is no endpoint to delete servers, backups, or files.
6. **Non-destructive edits.** Saving `server.properties` writes a `.bak` copy
   first and preserves leading comments and existing key ordering.
7. **Graceful stop only.** Stopping sends `SIGTERM`; there is no automatic
   `SIGKILL`. A process that does not exit within the timeout is reported as a
   failed stop for manual operator action.
8. **PID safety.** Status checks verify the process is alive *and* its cwd
   matches `server_path`, to avoid acting on a recycled PID.

## Input validation

- Registration validates that `server_path` is an existing directory and that
  the chosen jar is among detected candidates.
- Pydantic schemas bound string lengths on memory fields and names.
- Property save only persists keys that already exist in the file.

## Known v0.1 limitations (accepted)

- No CSRF protection (single-user LAN assumption).
- No rate limiting.
- No transport encryption (HTTP only).
- The app user has read/write access to managed server folders by necessity.

## Deferred to v0.2+

- Authentication and session management.
- HTTPS / TLS termination.
- RCON-based stop (graceful in-game `save-all` + `stop`).
- systemd integration with a tightly scoped, audited privilege boundary.
- CSRF tokens and audit logging.
