# 11 · Mod / Plugin Management (v0.3)

The server detail page gained **Mods** and **Plugins** sections. They are handled
identically — the only difference is the on-disk subdirectory (`mods/` vs
`plugins/`) under the server's path.

## What you can do

- **List** the jars in `mods/` and `plugins/`, with size and enabled/disabled
  state.
- **Upload** a `.jar` into either collection.
- **Enable / disable** a jar.
- There is **no delete** — disabling is the strongest action (consistent with the
  v0.1 "no file deletion" rule).

The directories are created automatically (created servers already have them; for
a registered server they are created on first upload).

## Enable / disable convention

The widely-used `.disabled` suffix is used:

| State    | Filename            |
|----------|---------------------|
| enabled  | `foo.jar`           |
| disabled | `foo.jar.disabled`  |

Toggling simply **renames** the file between the two names. A disabled jar is not
loaded by the server but is preserved on disk. The UI always labels the item by
its enabled base name (`foo.jar`) regardless of current state.

## Endpoints

All are POST and require an authenticated admin. `kind` ∈ `{mods, plugins}`:

| Route                                   | Action |
|-----------------------------------------|--------|
| `POST /servers/{id}/{kind}/upload`      | multipart `.jar` upload |
| `POST /servers/{id}/{kind}/toggle`      | form field `name=<file>.jar`; flips enabled/disabled |

## Safety

- Only `.jar` uploads are accepted; everything else is rejected with a message.
- The uploaded filename is reduced to its **basename** (any directory part is
  stripped) and the resolved target must be a direct child of the collection
  directory — no path traversal.
- The toggle target is validated the same way; a crafted `name` (e.g.
  `../../etc/passwd.jar`) cannot escape the collection directory.
- Uploads are streamed to a temp file and size-checked against
  `MAX_JAR_UPLOAD_BYTES` before being committed, so an oversized upload never
  lands as a truncated jar.

## Without a real Minecraft server

Listing, upload, and enable/disable are all fully verifiable with dummy `.jar`
files — no running server is required. Whether a given mod/plugin is actually
*compatible* with your server obviously still requires a real server to confirm.

Implementation: [`app/services/mod_service.py`](../app/services/mod_service.py).
