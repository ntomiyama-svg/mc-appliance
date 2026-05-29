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

## v0.3 — Server Creation Suite ✅ (partially delivered)

Delivered in v0.3:

- **GUI server creation** ✅ — create a new server (directory, `eula.txt`,
  `server.properties`, jar upload, DB row) without a shell. See
  [`10_server_creation.md`](10_server_creation.md).
- **Mod / plugin management** ✅ — list, upload `.jar`, enable/disable (no
  delete). See [`11_mod_plugin_management.md`](11_mod_plugin_management.md).
- **Scheduled backups** ✅ — per-server daily/interval schedules run by an
  in-process daemon thread (no new dependency). See
  [`12_backup_schedule.md`](12_backup_schedule.md).
- **dev/prod config resolution** ✅ — explicit `MC_APPLIANCE_CONFIG` wins, then a
  dev `./data/config.yaml`, then `/etc/mc-appliance/config.yaml`; an unreadable
  `/etc` config degrades to a warning + defaults in dev.

Still pending for v0.3 / carried forward:

- **Backup restore** (currently intentionally absent).
- **Backup retention pruning.** `retention_count` is stored and shown, but old
  backups are **not** deleted yet — file deletion stays out of scope under the
  v0.1 security model. This needs an explicit, audited delete path (with
  confirmation + a version bump) before pruning can be enabled.
- **Multi-user roles** (admin / operator / read-only).
- **Per-server resource limits** and start-on-boot toggles.

### Per-server systemd (future design — not in v0.3)

v0.3 keeps the v0.1 `subprocess`-based launch for both registered *and* created
servers; it does **not** generate per-server systemd units. The intended future
design:

- For each managed server, generate a `mc-server@<name>.service` template unit
  running as the unprivileged `mcapp` user with `WorkingDirectory` set to the
  server path and `ExecStart` set to the built java command.
- Writing units to `/etc/systemd/system` and running `systemctl
  daemon-reload`/`enable`/`start` requires privilege the web app deliberately
  does **not** have. The boundary would be a **small, audited, allow-list-only
  privileged helper** (e.g. a narrowly-scoped `sudoers` entry or a setuid helper
  restricted to `mc-server@*.service`), never general `sudo`/`systemctl` access.
- Because `services/server_process` already isolates process concerns, the
  supervisor backend can be swapped from `subprocess` to systemd without touching
  routers. This stays gated behind an explicit version bump + security review.

## v0.4 — Vanilla server jar cache ✅ (delivered)

Delivered in v0.4:

- **Vanilla jar cache** ✅ — fetch the official Minecraft Java Edition Vanilla
  `server.jar` from Mojang's version manifest, verify it (SHA-1 when provided),
  and cache it under `JAR_CACHE_DIR`. The Create Server page can then use a
  cached jar instead of a browser upload. See
  [`13_vanilla_jar_cache.md`](13_vanilla_jar_cache.md).

Scope / deliberate limits (v0.4):

- **Vanilla only.** Paper / Fabric / Forge / NeoForge auto-fetch is **not**
  implemented — those server types still require a jar upload. This is the seam
  for future per-loader downloaders.
- **No arbitrary-URL downloads.** Only the `server.jar` URL resolved *through*
  the Mojang manifest is ever fetched, and only over HTTPS to an allow-listed
  Mojang host. There is no free-form URL field.
- The cache is **separate** from running servers and from RCON — caching a jar
  never starts anything. **EULA agreement is still required** at server-creation
  time, exactly as before.
- The feature needs **outbound internet** to Mojang. In an offline environment,
  keep using the upload path; the dashboard warning and checks degrade silently
  (a failed external check never brings the app down).

Still pending / carried forward for jar management:

- Per-loader (Paper/Fabric/Forge/NeoForge) downloaders behind their own APIs.
- A background scheduler that periodically re-checks/downloads the latest
  release (the `VANILLA_CHECK_INTERVAL_HOURS` setting is reserved for this; v0.4
  checks on-demand from the dashboard and the JAR Cache page).
- Pruning of old cached jars (blocked by the same "no file deletion" rule as
  backup retention).

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
