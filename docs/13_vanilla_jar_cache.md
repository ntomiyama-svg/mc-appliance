# 13 · Vanilla Server Jar Cache (v0.4)

Since **v0.4** mc-appliance can fetch the official **Vanilla** Minecraft Java
Edition `server.jar` straight from Mojang's version manifest and cache it
locally, so a new server can be created from a cached jar instead of uploading
one from your PC.

> **Scope:** This is **Vanilla-only**. Paper / Fabric / Forge / NeoForge
> auto-fetch is intentionally **not** implemented — those server types still
> require a jar upload. The cache is a **separate** feature from RCON and from
> running servers; caching a jar never launches anything. Creating a server
> still **requires accepting the Minecraft EULA**.

## What it does

1. Fetches Mojang's `version_manifest_v2.json`.
2. Reads `latest.release` (the current stable Vanilla version).
3. Looks up that version's entry in `versions[]` and fetches its metadata JSON.
4. Reads `downloads.server.url` (and `sha1` / `size` when present).
5. Downloads the `server.jar` into the cache, verifying SHA-1 if provided.
6. Records the result in the `server_jar_cache` table.

Cached jars then appear as choices on **Create Server**.

## Where jars are cached

Cached jars live under `JAR_CACHE_DIR`, one directory per version:

```
<JAR_CACHE_DIR>/vanilla/<version>/server.jar
```

`JAR_CACHE_DIR` defaults to `DATA_DIR/jars`, i.e.:

- **dev checkout:** `./data/jars`
- **system install:** `/var/lib/mc-appliance/jars`

It can be overridden with the `MC_APPLIANCE_JAR_CACHE_DIR` environment variable
or `jar_cache_dir:` in `config.yaml`.

## Using it (UI)

1. Open **JAR Cache** in the sidebar (`/jars/vanilla`).
2. **Check latest** — asks Mojang for the current `latest.release` and whether it
   is already cached (no download).
3. **Download latest** — downloads the latest release `server.jar` into the
   cache. **Refresh cache list** reloads the table.
4. The table shows each cached version, type, size, download time, whether it is
   the latest, and whether the file is present on disk.

Then, on **Servers → Create Server**, pick a **server.jar source**:

- **Upload server.jar** — the original behaviour (upload a `.jar`).
- **Use cached latest Vanilla server.jar** — copies the cached latest jar.
- **Use cached Vanilla version** — copies a specific cached version you choose.

When a cached jar is used it is **copied** to `<server>/server.jar` and the
server's `minecraft_version` is recorded. EULA agreement is still mandatory.

> Cached-jar sources are only offered for the **Vanilla** server type. For any
> other type the form falls back to upload-only.

## API

All routes require an authenticated admin (global auth middleware). The POST
endpoints return JSON for the in-page buttons.

| Method + path                       | Purpose                                   |
| ----------------------------------- | ----------------------------------------- |
| `GET  /jars/vanilla`                | JAR Cache page (cached list + status)     |
| `POST /jars/vanilla/check-latest`   | Resolve `latest.release`, report if cached|
| `POST /jars/vanilla/download-latest`| Download the latest release server.jar    |
| `POST /jars/vanilla/download/{ver}` | Download a specific version's server.jar  |

## Configuration

| Setting (config.py)             | Env var                                  | Default |
| ------------------------------- | ---------------------------------------- | ------- |
| `JAR_CACHE_DIR`                 | `MC_APPLIANCE_JAR_CACHE_DIR`             | `DATA_DIR/jars` |
| `VANILLA_MANIFEST_URL`          | `MC_APPLIANCE_VANILLA_MANIFEST_URL`      | Mojang `version_manifest_v2.json` |
| `VANILLA_AUTO_CHECK_ENABLED`    | `MC_APPLIANCE_VANILLA_AUTO_CHECK`        | `true`  |
| `VANILLA_AUTO_DOWNLOAD_ENABLED` | `MC_APPLIANCE_VANILLA_AUTO_DOWNLOAD`     | `false` |
| `VANILLA_CHECK_INTERVAL_HOURS`  | `MC_APPLIANCE_VANILLA_CHECK_INTERVAL_HOURS` | `24` |
| `VANILLA_HTTP_TIMEOUT_SECONDS`  | `MC_APPLIANCE_VANILLA_HTTP_TIMEOUT`      | `15`    |

`VANILLA_AUTO_CHECK_ENABLED` controls whether the dashboard / JAR Cache page
reach out to Mojang to learn the latest release. `VANILLA_AUTO_DOWNLOAD_ENABLED`
controls whether a missing-latest situation may trigger an automatic download
(off by default — downloads are explicit). `VANILLA_CHECK_INTERVAL_HOURS` is
reserved for a future background re-check; v0.4 checks on demand only.

## Security model

- **No arbitrary-URL downloads.** The caller never supplies a URL — only a
  version id, which is validated (`^[A-Za-z0-9._-]{1,64}$`) and looked up in the
  manifest. The only URLs ever fetched are the manifest, the per-version
  metadata URL listed in it, and the `downloads.server.url` listed in that
  metadata.
- Every fetched URL must be **HTTPS** and on an allow-listed Mojang host
  (`*.mojang.com` / `*.minecraft.net`) — defence in depth on top of the manifest.
- Downloads only ever land **inside `JAR_CACHE_DIR`** (path traversal guarded by
  the allow-listed version id and a resolved-path check).
- Downloads stream to a temp file and are **atomically moved** into place only
  after a successful (and, when a SHA-1 is provided, verified) download — a
  failed/partial download never leaves a usable-looking jar behind.
- Re-downloading a version overwrites only that **same version**, never an
  unrelated jar.
- The download URL may appear in logs (one terse INFO line per download); no
  credentials are involved.
- This feature requires **outbound internet** to Mojang. External-communication
  failures are swallowed on the dashboard so the app keeps working offline — in
  that case, use the upload path.

## Dashboard warning

When `VANILLA_AUTO_CHECK_ENABLED` is on, the dashboard shows a warning if the
latest Vanilla release is not cached, linking to the JAR Cache page. The check
is best-effort: any external failure is silently ignored (it never breaks the
dashboard).

## Database

A new table `server_jar_cache` records each cached jar:

`id, server_type, version, release_type, source, jar_path, manifest_url,
download_url, sha1, size_bytes, is_latest, downloaded_at, created_at,
updated_at` — unique on `(server_type, version)`.

It is created automatically by `Base.metadata.create_all` on startup (no Alembic
yet; consistent with the rest of the project's lightweight migration approach).
