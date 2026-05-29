# 10 · Server Creation (v0.3)

Before v0.3 the app could only **register** an existing Minecraft server folder.
v0.3 adds the **Server Creation Suite**: you can create a brand-new server from
the GUI — directory, `server.properties`, `eula.txt`, `mods/` + `plugins/`, and
the uploaded `server.jar` — without touching a shell.

> This stays within the v0.1 security model: the web app never runs as root,
> never shells out, never deletes anything, and confines every write to the new
> server directory.

## Where servers are created

New servers are created under a configurable parent directory, `SERVERS_DIR`:

| Environment | Default `SERVERS_DIR`            |
|-------------|----------------------------------|
| dev checkout| `./data/servers`                 |
| systemd install | `/var/lib/mc-appliance/servers` |

`SERVERS_DIR` is derived from `DATA_DIR` (so a system install's
`/var/lib/mc-appliance` data dir yields `/var/lib/mc-appliance/servers`). Override
it explicitly with the `MC_APPLIANCE_SERVERS_DIR` env var or `servers_dir:` in
`config.yaml`. The directory is created at startup; under systemd it falls inside
the unit's `ReadWritePaths=/var/lib/mc-appliance`.

The final path of a created server is `SERVERS_DIR/<server_name>`.

## Creating a server

1. **Servers → Create Server** (`/servers/create`).
2. Fill in the form:
   - **Server name** — the folder name. Restricted to `[A-Za-z0-9_-]`, 1–64
     chars (this is what makes path traversal impossible).
   - **Server type** — Vanilla / Paper / Fabric / Forge / NeoForge / Custom
     (stored as `minecraft_type`).
   - **Server port, min/max memory, MOTD, max players, gamemode, difficulty,
     online-mode**.
   - **enable-rcon / rcon.port / rcon.password** (optional).
   - **server.jar** upload (optional, `.jar` only).
   - **EULA checkbox** — *required*.
3. **Create server**. You are redirected to the new server's detail page.

### What gets written

| Artifact            | Notes |
|---------------------|-------|
| `SERVERS_DIR/<name>/` | created fresh; an existing directory is an error |
| `eula.txt`          | `eula=true` — **only** when the EULA box is ticked |
| `server.properties` | generated from the form (see below) |
| `mods/`, `plugins/` | empty, ready for uploads |
| `server.jar`        | the uploaded jar, always renamed to `server.jar` |
| DB `servers` row    | `jar_file=server.jar`, `java_path=/usr/bin/java`, `status=stopped`, RCON settings mirrored |

### Generated `server.properties`

Keys written: `server-port`, `motd`, `max-players`, `gamemode`, `difficulty`,
`online-mode`, `level-name=world`, and `enable-rcon`. When RCON is enabled,
`rcon.port` and (if provided) `rcon.password` are also written. The format is
plain `key=value` lines, identical to what
[`properties_service`](../app/services/properties_service.py) reads/writes, so
the existing **Edit properties** screen works on a generated file with no
surprises.

## Validation & safety

- **Name** must match `[A-Za-z0-9_-]{1,64}`; anything else is rejected before any
  disk write.
- **EULA** must be accepted, or creation is refused (no directory is created).
- **Duplicate name** (existing DB server *or* existing directory) is rejected.
- **Ports** must be 1–65535; **max players** 1–1000.
- **Jar upload**: `.jar` only, capped at `MAX_JAR_UPLOAD_BYTES` (default 500 MiB,
  override with `MC_APPLIANCE_MAX_JAR_BYTES`). The upload is buffered and
  size-checked *before* the directory is created, so a bad upload never leaves a
  half-built tree. It is always stored as `server.jar` — an arbitrary save path
  is impossible.
- All POSTs require an authenticated admin (global `AuthMiddleware`).

## Without a real Minecraft server

Everything above is verifiable with a dummy/empty jar (or none at all): directory
creation, `eula.txt`, `server.properties`, the jar upload, the DB row, and the
`mods/` + `plugins/` scaffolding. **Starting** the server still requires a real,
runnable jar and a working Java — that part is unchanged from v0.1
(`subprocess`-based launch).

## Future

Per-server **systemd** unit generation is intentionally *not* part of v0.3 — see
[`06_future_plan.md`](06_future_plan.md). Servers created here are launched as
managed subprocesses exactly like registered ones.
