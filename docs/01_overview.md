# 01 · Overview

## Purpose

mc-appliance provides a simple, NAS-style web interface for managing existing
Minecraft servers on a Linux host. It is aimed at home / small-team operators
who run one or more Minecraft servers and want browser-based control without
SSH, without a desktop GUI, and without containers.

## Design goals

- **Add-on, not an OS.** Installs onto an existing Linux server. A Rocky Linux 9
  appliance image is a future possibility, not a v0.1 requirement.
- **Safe by default.** Never runs as root; no privileged operations in v0.1.
- **Approachable UI.** Looks and feels like a NAS admin panel.
- **Incremental.** v0.1 is a working MVP; systemd, RCON, auth and HTTPS come
  later by design.

## Primary target platform

- Rocky Linux 9 (RHEL 9 family)
- Python 3.9+
- Future: Ubuntu / Debian

## Feature scope (v0.1)

| Area              | v0.1                                              |
|-------------------|---------------------------------------------------|
| Server list       | ✅ list, status, actions                          |
| Registration      | ✅ register existing folder + auto-detect         |
| Lifecycle         | ✅ start / stop / restart via subprocess          |
| Status            | ✅ reconciled against the real process            |
| Logs              | ✅ tail of `logs/latest.log` (200 lines)          |
| Properties        | ✅ view + edit `server.properties` (+ `.bak`)     |
| Backups           | ✅ manual zip backup + history                    |
| Metrics           | ✅ CPU / memory / disk via psutil                 |
| System info       | ✅ OS / Python / Java / disk / IP                 |
| Auth / HTTPS      | ❌ deferred to v0.2+                               |
| systemd / RCON    | ❌ deferred to v0.2+                               |
| Restore / delete  | ❌ out of scope                                   |

## Non-goals (for now)

- Multi-tenant hosting / billing
- Mod/plugin marketplace
- Cluster orchestration
- Replacing systemd as a long-term process supervisor (v0.2 will integrate it)
