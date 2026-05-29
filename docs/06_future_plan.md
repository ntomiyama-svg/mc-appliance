# 06 · Future Plan

## v0.2 — Production-readiness basics

- **Authentication** ✅ (delivered in v0.1.2): single-admin session login. No
  unauthenticated access to any mutating endpoint.
- **RCON integration** ✅ (delivered in v0.2): graceful stop via `save-all` +
  `stop` over RCON with a SIGTERM fallback; `save-all` before backups; an
  allow-listed in-page console. See [`09_rcon.md`](09_rcon.md).
- **HTTPS**: TLS support (self-signed for LAN, or reverse-proxy guidance).
  *(still pending)*
- **systemd integration**: optionally generate and manage per-server systemd
  units (with a clearly scoped, audited privilege boundary) so servers survive
  host reboots and get proper supervision. *(still pending)*
- **CSRF protection** and basic audit logging. *(still pending)*

### Carried forward from v0.2 (not yet done)

- HTTPS / TLS for the web UI.
- Per-server systemd unit generation/management.
- CSRF tokens and audit logging.
- Multi-user accounts and roles.
- An audited "advanced" RCON mode for free-form commands (the v0.2 allow-list is
  the seam for this).

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
