# 16 · Java Runtime Manager & Java Installer (v0.6)

The **Java Runtime Manager** screen (`/system/java`, also linked from the
**System** page) shows which Java runtimes are installed on the host and lets an
operator — **when explicitly enabled** — install one of the supported Java
versions straight from the GUI.

> **Default: GUI install is DISABLED.** Out of the box the manager is
> **read-only**: it detects runtimes and shows install commands, but the
> *Install Java* buttons are inert and the page shows *"GUI install is not
> enabled"*. Installation is opt-in (see [Enabling](#enabling-the-gui-installer)).

## Why a dedicated helper instead of running dnf/apt from the app

The hard rules in `CLAUDE.md` still apply:

- the web app **never runs as root**,
- **no arbitrary command execution**,
- **no arbitrary package names**.

So the router never calls a package manager directly. Instead it shells out —
through `sudo` — to a single, fixed helper script,
`scripts/install_java_runtime.sh`, passing it **one argument: the Java major
version**. The script:

- accepts only the allow-listed major versions **8 / 11 / 17 / 21 / 25**
  (anything else is rejected before any package manager runs),
- detects the OS family (`dnf` ⇒ Rocky/RHEL, `apt` ⇒ Ubuntu/Debian),
- maps the version to a **hard-coded** package name (the caller can never supply
  a package name),
- prints all progress to stdout/stderr (shown in the GUI), and
- never handles or prints secrets.

### Package names per OS

| Java | Rocky / RHEL (dnf)              | Ubuntu / Debian (apt)        |
|------|--------------------------------|------------------------------|
| 8    | `java-1.8.0-openjdk-headless`  | `openjdk-8-jre-headless`     |
| 11   | `java-11-openjdk-headless`     | `openjdk-11-jre-headless`    |
| 17   | `java-17-openjdk-headless`     | `openjdk-17-jre-headless`    |
| 21   | `java-21-openjdk-headless`     | `openjdk-21-jre-headless`    |
| 25   | `java-25-openjdk-headless`     | `openjdk-25-jre-headless`    |

> **Not every version is packaged everywhere.** Older or very new versions may
> simply not exist in a given distribution's repositories — **Java 25 in
> particular is not yet shipped by every distro**, and some distros lack older
> ones. When the package is unavailable the install **fails clearly** and the
> package-manager output is shown in the GUI. The manager always also displays a
> **manual install command** you can run yourself or adapt to another source.

## Enabling the GUI installer

Installation is gated by a **minimal `sudoers` rule** that lets the service
account run *only* the helper script with *only* an allowed version — no
wildcards, no free-form commands.

### Option A — via `install.sh` (recommended)

Re-run the system installer with `ENABLE_JAVA_INSTALLER=1`:

```bash
sudo ENABLE_JAVA_INSTALLER=1 bash scripts/install.sh
```

This deploys the helper to a fixed, **root-owned** path (outside the app tree,
so a compromised app cannot rewrite the script it runs as root) and writes the
sudoers rule:

- Helper: `/usr/local/lib/mc-appliance/install_java_runtime.sh` (root:root 0755)
- Sudoers: `/etc/sudoers.d/mc-appliance-java` (root:root 0440), validated with
  `visudo -c` before being installed.

### Option B — manual sudoers

Copy the helper to `/usr/local/lib/mc-appliance/install_java_runtime.sh`
(root-owned, mode 0755), then create `/etc/sudoers.d/mc-appliance-java` with one
line **per allowed version** (replace `mcapp` with your service account):

```sudoers
mcapp ALL=(root) NOPASSWD: /usr/local/lib/mc-appliance/install_java_runtime.sh 8
mcapp ALL=(root) NOPASSWD: /usr/local/lib/mc-appliance/install_java_runtime.sh 11
mcapp ALL=(root) NOPASSWD: /usr/local/lib/mc-appliance/install_java_runtime.sh 17
mcapp ALL=(root) NOPASSWD: /usr/local/lib/mc-appliance/install_java_runtime.sh 21
mcapp ALL=(root) NOPASSWD: /usr/local/lib/mc-appliance/install_java_runtime.sh 25
```

Always validate with `visudo -cf /etc/sudoers.d/mc-appliance-java`.

> The app discovers whether the rule exists with a non-mutating probe
> (`sudo -n -l <helper> <version>`) — it never needs to read `/etc/sudoers.d`.
> Remove the sudoers file to disable the GUI installer again.

## How the app decides what to show

`app/services/java_runtime.py` assembles the screen:

- **Detected Java Runtimes** — probes `java` on `PATH`, the registration-form
  default, and `/usr/lib/jvm/*/bin/java`, running `java -version` on each and
  parsing the major version (handles both `1.8.0_x` and `17.x` forms).
- **Recommended Java** — `config.JAVA_RECOMMENDED_VERSION` (Java 21).
- **Required Java** — inferred from the registered servers'
  `minecraft_version`: `≤1.16 → 8`, `1.17–1.20.4 → 17`, `1.20.5+/1.21+ → 21`.
  Falls back to the recommended version when nothing can be inferred.
- **Not installed** — allowed versions minus detected ones.

## API

All endpoints are under the existing auth middleware (login required) and share
the app's `SameSite=Lax` session-cookie CSRF mitigation.

| Method & path                | Purpose                                                  |
|------------------------------|----------------------------------------------------------|
| `GET /system/java`           | HTML manager page; returns the JSON inventory instead when called with `Accept: application/json` or `?format=json`. |
| `POST /system/java/rescan`   | Re-detect runtimes; returns the fresh JSON inventory.    |
| `POST /system/java/install`  | Body `java_version` (8/11/17/21/25 only). Validates the version, runs the helper via sudo, returns `{ok, message, returncode, stdout, stderr, command, info}`. |

A non-allowed or non-numeric `java_version` is rejected with **HTTP 400** before
sudo is ever touched. When the sudoers rule is absent, install returns
`ok:false` with *"GUI install is not enabled"* rather than erroring.

## Logging

GUI-initiated installs are recorded to:

- **System install:** `/var/log/mc-appliance/java_install.log`
- **Dev checkout:** `./logs/java_install.log`

The helper also best-effort appends to `/var/log/mc-appliance/java_install.log`
when it can write there (it runs as root via sudo). stdout/stderr are shown in
the GUI. No passwords or secrets are logged (the feature handles none).

## Constraints (intentional)

- The installer is **additive**: it never switches the system `alternatives` /
  default `java`. Each server keeps choosing its own `java_path`.
- A Java version not packaged by the OS is treated as a **failure**, surfaced
  with the package-manager output — it is not silently ignored.
- Only the five allow-listed major versions are ever installable.
