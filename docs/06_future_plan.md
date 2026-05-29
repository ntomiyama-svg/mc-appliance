# 06 · Future Plan

## v0.2 — Production-readiness basics

- **Authentication**: local user accounts + session login. No unauthenticated
  access to any mutating endpoint.
- **HTTPS**: TLS support (self-signed for LAN, or reverse-proxy guidance).
- **RCON integration**: graceful stop via `save-all` + `stop` over RCON instead
  of raw `SIGTERM`; ability to send console commands.
- **systemd integration**: optionally generate and manage per-server systemd
  units (with a clearly scoped, audited privilege boundary) so servers survive
  host reboots and get proper supervision.
- **CSRF protection** and basic audit logging.

## v0.3 — Operations features

- **Backup restore** (currently intentionally absent).
- **Scheduled backups** with retention policies.
- **Multi-user roles** (admin / operator / read-only).
- **Per-server resource limits** and start-on-boot toggles.

## Later

- **Rocky Linux 9 appliance image**: a packaged, opinionated image where
  mc-appliance is preinstalled and hardened.
- **Ubuntu / Debian support**: distro-specific install scripts and package
  names; CI coverage on multiple distros.
- **Mod/plugin awareness**: list installed mods/plugins and detect versions.
- **Metrics history**: store and chart CPU/memory/TPS over time.
- **Notifications**: alerts on crash / high resource usage.

## Migration notes (v0.1 → v0.2)

- The v0.1 `pid`-in-DB process model will be superseded by systemd/RCON. The
  `Server` model already isolates process concerns in `services/server_process`
  so the supervisor backend can be swapped without touching routers.
- Adding auth will introduce a `users` table and a session/middleware layer;
  existing routes stay the same but gain a dependency guard.
- The SIGKILL escalation question is deferred: v0.2 should prefer RCON `stop`,
  falling back to an **explicit, confirmed** force-kill rather than an
  automatic one.
