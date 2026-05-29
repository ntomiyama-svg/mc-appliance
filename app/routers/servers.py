"""Server list, registration, detail and lifecycle actions."""
from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import (
    DEFAULT_JAVA_PATH,
    DEFAULT_MAX_MEMORY,
    DEFAULT_MIN_MEMORY,
    DEFAULT_RCON_HOST,
    DEFAULT_RCON_PORT,
    DEFAULT_RETENTION_COUNT,
    DIFFICULTIES,
    GAMEMODES,
    JAVA_RECOMMENDED_VERSION,
    SERVER_TYPES,
    SERVERS_DIR,
)
from app.database import get_db
from app.models import (
    SCHEDULE_DAILY,
    SCHEDULE_INTERVAL,
    STATUS_STOPPED,
    Backup,
    BackupSchedule,
    Server,
)
from app.services import (
    backup_service,
    java_runtime,
    log_service,
    mod_service,
    properties_service,
    rcon_service,
    server_console,
    server_creator,
    server_detector,
    server_process,
    vanilla_downloader,
)
from app.config import CONSOLE_LOG_DEFAULT_LINES, CONSOLE_LOG_MAX_LINES
from app.templating import templates

router = APIRouter(prefix="/servers")


# Defaults pre-filled into the Create Server form.
_CREATE_DEFAULTS = {
    "server_name": "",
    "server_type": SERVER_TYPES[0],
    "server_port": "25565",
    "min_memory": DEFAULT_MIN_MEMORY,
    "max_memory": DEFAULT_MAX_MEMORY,
    "motd": "A Minecraft Server",
    "max_players": "20",
    "gamemode": "survival",
    "difficulty": "normal",
    "online_mode": True,
    "enable_rcon": False,
    "rcon_port": str(DEFAULT_RCON_PORT),
    # v0.4: how the server.jar is provided — "upload", "cached_latest" or
    # "cached_version" (the latter pairs with cached_version below).
    "jar_source": "upload",
    "cached_version": "",
    # v0.5: per-server Java runtime, chosen at create time. Empty means "let the
    # context pre-select the recommended runtime (or fall back to the default)".
    "java_path": "",
}


def _cached_vanilla_choices(db: Session):
    """Return cached Vanilla jars (file present) for the create-form dropdown."""
    rows = vanilla_downloader.list_cached_vanilla_jars(db)
    return [r for r in rows if vanilla_downloader.jar_exists(r)]


def _recommended_major_for_create(db: Session) -> int:
    """Best-effort recommended Java for the create form.

    The exact Minecraft version is only known up front when a cached Vanilla jar
    is used, so we derive the recommendation from the latest cached jar when
    present, otherwise fall back to the global recommended version.
    """
    latest = vanilla_downloader.get_latest_cached_jar(db)
    if latest is not None and latest.version:
        major = java_runtime.minecraft_version_to_java(latest.version)
        if major is not None:
            return major
    return JAVA_RECOMMENDED_VERSION


def _create_context(form: dict, db: Session, errors=None) -> dict:
    cached = _cached_vanilla_choices(db)
    latest = next((r for r in cached if r.is_latest), None)

    # v0.5 Java runtime selection for the create form.
    runtimes = java_runtime.detect_runtimes()
    recommended_major = _recommended_major_for_create(db)
    recommended_rt = java_runtime.runtime_for_major(recommended_major, runtimes)
    # Pre-select: an explicit form value, else the recommended runtime, else the
    # default java path (so the dropdown always has a sensible selection).
    selected_java = (form.get("java_path") or "").strip()
    if not selected_java:
        selected_java = recommended_rt["path"] if recommended_rt else DEFAULT_JAVA_PATH

    return {
        "title": "Create Server",
        "server_types": SERVER_TYPES,
        "gamemodes": GAMEMODES,
        "difficulties": DIFFICULTIES,
        "servers_dir": str(SERVERS_DIR),
        "form": form,
        "errors": errors or [],
        # v0.4 cached-jar options.
        "cached_jars": cached,
        "cached_latest": latest,
        # v0.5 Java runtime selection.
        "java_runtimes": runtimes,
        "java_recommended_major": recommended_major,
        "java_recommended_installed": recommended_rt is not None,
        "java_selected_path": selected_java,
    }


def _get_server_or_404(db: Session, server_id: int) -> Server:
    server = db.get(Server, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


@router.get("", response_class=HTMLResponse)
def list_servers(request: Request, db: Session = Depends(get_db)):
    servers = db.query(Server).order_by(Server.name).all()
    for server in servers:
        server_process.refresh_status(db, server)
    return templates.TemplateResponse(
        request, "servers.html", {"title": "Servers", "servers": servers}
    )


@router.get("/register", response_class=HTMLResponse)
def register_form(
    request: Request,
    path: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Show the registration form. If ``path`` is given, run detection on it."""
    detection = None
    if path:
        detection = server_detector.detect_server(path)
    return templates.TemplateResponse(
        request,
        "server_register.html",
        {
            "title": "Register Server",
            "detection": detection,
            "path": path or "",
            "defaults": {
                "java_path": DEFAULT_JAVA_PATH,
                "min_memory": DEFAULT_MIN_MEMORY,
                "max_memory": DEFAULT_MAX_MEMORY,
            },
        },
    )


@router.post("/register")
def register_server(
    request: Request,
    name: str = Form(...),
    server_path: str = Form(...),
    java_path: str = Form(DEFAULT_JAVA_PATH),
    min_memory: str = Form(DEFAULT_MIN_MEMORY),
    max_memory: str = Form(DEFAULT_MAX_MEMORY),
    jar_file: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    server_path = server_path.strip()
    name = name.strip()
    base = Path(server_path)

    errors = []
    if not name:
        errors.append("Name is required.")
    if not base.is_dir():
        errors.append(f"Server path is not a directory: {server_path}")
    if db.query(Server).filter(Server.name == name).first():
        errors.append(f"A server named '{name}' is already registered.")

    detection = server_detector.detect_server(server_path) if base.is_dir() else None

    # jar_file must be one of the detected candidates under server_path.
    chosen_jar = (jar_file or "").strip() or None
    if detection and detection.jar_candidates:
        if chosen_jar is None:
            chosen_jar = detection.jar_candidates[0]
        elif chosen_jar not in detection.jar_candidates:
            errors.append("Selected jar is not located inside the server path.")
    elif detection is not None and not detection.jar_candidates:
        errors.append("No .jar file was found in the server path.")

    if errors:
        return templates.TemplateResponse(
            request,
            "server_register.html",
            {
                "title": "Register Server",
                "detection": detection,
                "path": server_path,
                "errors": errors,
                "form": {
                    "name": name,
                    "java_path": java_path,
                    "min_memory": min_memory,
                    "max_memory": max_memory,
                },
                "defaults": {
                    "java_path": DEFAULT_JAVA_PATH,
                    "min_memory": DEFAULT_MIN_MEMORY,
                    "max_memory": DEFAULT_MAX_MEMORY,
                },
            },
            status_code=400,
        )

    server = Server(
        name=name,
        server_path=str(base.resolve()),
        jar_file=chosen_jar,
        java_path=java_path.strip() or DEFAULT_JAVA_PATH,
        min_memory=min_memory.strip() or DEFAULT_MIN_MEMORY,
        max_memory=max_memory.strip() or DEFAULT_MAX_MEMORY,
        minecraft_type=detection.minecraft_type if detection else "Unknown",
        minecraft_version=detection.minecraft_version if detection else None,
        has_mods=detection.has_mods if detection else False,
        has_plugins=detection.has_plugins if detection else False,
    )
    server.start_command = " ".join(server_process.build_start_command(server))
    db.add(server)
    db.commit()
    db.refresh(server)

    return RedirectResponse(url=f"/servers/{server.id}", status_code=303)


# ----- Create a brand-new server (v0.3) ---------------------------------------
#
# Unlike /register (which adopts an existing folder), /create lays down a new
# directory, eula.txt, server.properties, mods/ + plugins/, and the uploaded
# server.jar — all inside SERVERS_DIR. Defined before /{server_id} so the int
# converter never shadows it.


@router.get("/create", response_class=HTMLResponse)
def create_form(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "server_create.html", _create_context(dict(_CREATE_DEFAULTS), db)
    )


@router.post("/create")
async def create_server(
    request: Request,
    server_name: str = Form(...),
    server_type: str = Form(SERVER_TYPES[0]),
    server_port: str = Form("25565"),
    min_memory: str = Form(DEFAULT_MIN_MEMORY),
    max_memory: str = Form(DEFAULT_MAX_MEMORY),
    motd: str = Form("A Minecraft Server"),
    max_players: str = Form("20"),
    gamemode: str = Form("survival"),
    difficulty: str = Form("normal"),
    online_mode: Optional[str] = Form(None),
    enable_rcon: Optional[str] = Form(None),
    rcon_port: str = Form(str(DEFAULT_RCON_PORT)),
    rcon_password: str = Form(""),
    eula_agree: Optional[str] = Form(None),
    jar_source: str = Form("upload"),
    cached_version: str = Form(""),
    java_path: str = Form(""),
    jar_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    def _checked(value: Optional[str]) -> bool:
        return (value or "").lower() in ("on", "true", "1", "yes")

    online_mode_b = _checked(online_mode)
    enable_rcon_b = _checked(enable_rcon)
    eula_b = _checked(eula_agree)
    server_type = server_type if server_type in SERVER_TYPES else SERVER_TYPES[0]

    # v0.4: jar source. Cached Vanilla jars are only offered for Vanilla servers;
    # any other type falls back to upload (Paper/Fabric/etc auto-fetch is not yet
    # implemented). Unknown source values degrade to "upload".
    jar_source = (jar_source or "upload").strip()
    if jar_source not in ("upload", "cached_latest", "cached_version"):
        jar_source = "upload"
    if server_type != "Vanilla" and jar_source != "upload":
        jar_source = "upload"
    cached_version = (cached_version or "").strip()

    # Echo the submitted values back if we have to re-render with errors.
    form = {
        "server_name": server_name,
        "server_type": server_type,
        "server_port": server_port,
        "min_memory": min_memory,
        "max_memory": max_memory,
        "motd": motd,
        "max_players": max_players,
        "gamemode": gamemode,
        "difficulty": difficulty,
        "online_mode": online_mode_b,
        "enable_rcon": enable_rcon_b,
        "rcon_port": rcon_port,
        "jar_source": jar_source,
        "cached_version": cached_version,
        "java_path": (java_path or "").strip(),
    }

    # Resolve the chosen Java runtime. Only a detected runtime is accepted; an
    # unknown/blank value falls back to the configured default so creation never
    # fails on this alone (the runtime can be changed later on the detail page).
    chosen_java = (java_path or "").strip()
    if not java_runtime.is_known_runtime_path(chosen_java):
        chosen_java = DEFAULT_JAVA_PATH

    def _fail(message, status=400):
        return templates.TemplateResponse(
            request,
            "server_create.html",
            _create_context(form, db, errors=[message]),
            status_code=status,
        )

    # Resolve the cached jar source (if any) up-front so we never build a
    # half-finished directory when the chosen jar is missing. ``cached_jar_path``
    # and ``cached_jar_version`` stay None for the upload path.
    cached_jar_path = None
    cached_jar_version = None
    if jar_source == "cached_latest":
        latest_row = vanilla_downloader.get_latest_cached_jar(db)
        if latest_row is None:
            return _fail(
                "No cached latest Vanilla jar is available. Download it from "
                "JAR Cache first, or upload a jar."
            )
        cached_jar_path = latest_row.jar_path
        cached_jar_version = latest_row.version
    elif jar_source == "cached_version":
        if not cached_version:
            return _fail("Choose a cached Vanilla version, or pick another jar source.")
        path = vanilla_downloader.get_cached_jar_path(db, cached_version)
        if path is None:
            return _fail(
                f"Cached Vanilla version '{cached_version}' is not available on disk."
            )
        cached_jar_path = str(path)
        cached_jar_version = cached_version

    # A jar upload is optional (you may not have one without a real server), but
    # buffer/validate it up-front so a bad upload never creates a half-built dir.
    jar_buffer = None
    has_upload = (
        jar_source == "upload"
        and jar_file is not None
        and bool((jar_file.filename or "").strip())
    )
    try:
        name = server_creator.validate_server_name(server_name)
        if db.query(Server).filter(Server.name == name).first():
            return _fail(f"A server named '{name}' is already registered.")

        port = server_creator.validate_port(server_port, "Server port")
        players = server_creator.validate_max_players(max_players)
        rcon_port_i = server_creator.validate_port(rcon_port, "RCON port")
        if not eula_b:
            return _fail("You must accept the Minecraft EULA to create a server.")

        if has_upload:
            jar_buffer = await server_creator.buffer_jar_upload(jar_file)

        # All validation passed — create the directory and lay down files.
        server_dir = server_creator.create_directory(name)
        server_creator.write_eula(server_dir, eula_b)

        props = server_creator.build_properties(
            {
                "server_port": str(port),
                "motd": (motd or "A Minecraft Server").strip(),
                "max_players": str(players),
                "gamemode": server_creator.normalize_gamemode(gamemode),
                "difficulty": server_creator.normalize_difficulty(difficulty),
                "online_mode": "true" if online_mode_b else "false",
                "enable_rcon": "true" if enable_rcon_b else "false",
                "rcon_port": str(rcon_port_i),
            },
            rcon_password=rcon_password.strip() or None,
        )
        server_creator.write_properties(server_dir, props)
        server_creator.ensure_mod_plugin_dirs(server_dir)
        if jar_buffer is not None:
            server_creator.save_jar(server_dir, jar_buffer)
        elif cached_jar_path is not None:
            server_creator.place_cached_jar(server_dir, cached_jar_path)
    except server_creator.CreationError as exc:
        return _fail(str(exc))
    finally:
        if jar_buffer is not None:
            jar_buffer.close()

    server = Server(
        name=name,
        server_path=str(server_dir.resolve()),
        jar_file="server.jar",
        java_path=chosen_java,
        min_memory=min_memory.strip() or DEFAULT_MIN_MEMORY,
        max_memory=max_memory.strip() or DEFAULT_MAX_MEMORY,
        status=STATUS_STOPPED,
        minecraft_type=server_type,
        # When we copied a cached Vanilla jar we know the exact version.
        minecraft_version=cached_jar_version,
        has_mods=True,
        has_plugins=True,
        rcon_enabled=enable_rcon_b,
        rcon_host=DEFAULT_RCON_HOST,
        rcon_port=rcon_port_i,
        rcon_password=(rcon_password.strip() or None),
    )
    server.start_command = " ".join(server_process.build_start_command(server))
    db.add(server)
    db.commit()
    db.refresh(server)

    if has_upload:
        jar_note = "server.jar uploaded. "
    elif cached_jar_version is not None:
        jar_note = f"Cached Vanilla {cached_jar_version} copied as server.jar. "
    else:
        jar_note = "No jar provided yet. "
    return RedirectResponse(
        url=f"/servers/{server.id}?msg=" + quote("Server created. " + jar_note),
        status_code=303,
    )


@router.get("/{server_id}", response_class=HTMLResponse)
def server_detail(server_id: int, request: Request, db: Session = Depends(get_db)):
    server = _get_server_or_404(db, server_id)
    server_process.refresh_status(db, server)
    detection = server_detector.detect_server(server.server_path)
    log_text = log_service.read_latest_log(server)
    start_command = " ".join(server_process.build_start_command(server))

    # Mods / plugins (v0.3). Listing is best-effort; a bad path just yields [].
    try:
        mods = mod_service.list_items(server, "mods")
    except mod_service.ModError:
        mods = []
    try:
        plugins = mod_service.list_items(server, "plugins")
    except mod_service.ModError:
        plugins = []

    schedule = (
        db.query(BackupSchedule)
        .filter(BackupSchedule.server_id == server.id)
        .order_by(BackupSchedule.id.desc())
        .first()
    )

    return templates.TemplateResponse(
        request,
        "server_detail.html",
        {
            "title": server.name,
            "server": server,
            "detection": detection,
            "log_text": log_text,
            "start_command": start_command,
            # RCON context. Never expose the password itself — only whether one
            # is set — and the list of commands the v0.2 console allows.
            "rcon_password_set": bool(server.rcon_password),
            "allowed_commands": rcon_service.ALLOWED_COMMANDS,
            # v0.5 per-server Java runtime selection.
            "java_info": java_runtime.server_java_info(server),
            # v0.3 additions.
            "mods": mods,
            "plugins": plugins,
            "schedule": schedule,
            "retention_default": DEFAULT_RETENTION_COUNT,
            # v0.6 backup / restore.
            "backups": backup_service.list_backups(db, server),
            # v0.5 console section.
            "console_default_lines": CONSOLE_LOG_DEFAULT_LINES,
            "console_max_lines": CONSOLE_LOG_MAX_LINES,
        },
    )


@router.get("/{server_id}/log/raw", response_class=HTMLResponse)
def server_log_raw(server_id: int, db: Session = Depends(get_db)):
    """Plain-text latest.log tail for AJAX refresh."""
    server = _get_server_or_404(db, server_id)
    return HTMLResponse(content=log_service.read_latest_log(server), media_type="text/plain")


# ----- Server console / live-log viewer (v0.5) --------------------------------
#
# All console routes are admin-only via the global AuthMiddleware. The JSON
# endpoints back the auto-refreshing Console section on the server detail page
# (and a standalone /console page); none of them ever expose a path outside the
# registered server_path or the managed-log directory, nor an RCON password.


def _console_context(server: Server) -> dict:
    """Shared template context for the console page / section."""
    return {
        "server": server,
        "rcon_password_set": bool(server.rcon_password),
        "allowed_commands": rcon_service.ALLOWED_COMMANDS,
        "console_default_lines": CONSOLE_LOG_DEFAULT_LINES,
        "console_max_lines": CONSOLE_LOG_MAX_LINES,
    }


@router.get("/{server_id}/console", response_class=HTMLResponse)
def console_page(server_id: int, request: Request, db: Session = Depends(get_db)):
    """Standalone Console page (the same section is also embedded in detail)."""
    server = _get_server_or_404(db, server_id)
    server_process.refresh_status(db, server)
    context = {"title": f"{server.name} · console", **_console_context(server)}
    return templates.TemplateResponse(request, "server_console.html", context)


@router.get("/{server_id}/console/status")
def console_status(server_id: int, db: Session = Depends(get_db)):
    """JSON snapshot of process + log state and the derived detected_state."""
    server = _get_server_or_404(db, server_id)
    # Evaluate first (reads the DB status as-is), then reconcile the persisted
    # status so a vanished process is eventually marked stopped without hiding a
    # crash from this very response.
    payload = server_console.evaluate(server)

    # Persist the detected runtime status and any parsed startup duration so they
    # survive without live polling. Only commit on a real change to avoid a write
    # on every 5s poll.
    changed = False
    detected = payload.get("detected_state")
    if detected and server.last_runtime_status != detected:
        server.last_runtime_status = detected
        changed = True
    duration = payload.get("startup_duration_seconds")
    if duration is not None:
        rounded = int(round(duration))
        if server.last_startup_duration_seconds != rounded:
            server.last_startup_duration_seconds = rounded
            changed = True
    if changed:
        db.commit()

    server_process.refresh_status(db, server)
    return JSONResponse(payload)


@router.get("/{server_id}/console/latest-log")
def console_latest_log(
    server_id: int,
    lines: int = Query(CONSOLE_LOG_DEFAULT_LINES),
    db: Session = Depends(get_db),
):
    """Tail of the server's own logs/latest.log (registered server_path only)."""
    server = _get_server_or_404(db, server_id)
    result = server_console.read_latest_log(server, server_console.clamp_lines(lines))
    return JSONResponse(result)


@router.get("/{server_id}/console/managed-log")
def console_managed_log(
    server_id: int,
    lines: int = Query(CONSOLE_LOG_DEFAULT_LINES),
    db: Session = Depends(get_db),
):
    """Tail of the mc-appliance-managed stdout/stderr log (MANAGED_LOG_DIR only)."""
    server = _get_server_or_404(db, server_id)
    result = server_console.read_managed_log(server, server_console.clamp_lines(lines))
    return JSONResponse(result)


@router.post("/{server_id}/console/rcon-command")
def console_rcon_command(
    server_id: int, command: str = Form(...), db: Session = Depends(get_db)
):
    """Run an allow-listed RCON command from the console (RCON-enabled only)."""
    server = _get_server_or_404(db, server_id)
    if not server.rcon_enabled:
        return JSONResponse(
            {
                "ok": False,
                "message": "RCON is disabled for this server.",
                "diagnosis": "Enable RCON, set a password, and restart the server "
                "before sending commands.",
                "response": None,
            }
        )
    # Reuses the existing allow-list + validation in rcon_service; the password
    # is never echoed back to the client.
    result = rcon_service.run_command(server, command)
    server.rcon_last_status = (result["message"] or "")[:250]
    db.commit()
    return JSONResponse(result)


def _action_redirect(server_id: int, message: str) -> RedirectResponse:
    # Pass a short status message back via query string for display.
    from urllib.parse import quote

    return RedirectResponse(
        url=f"/servers/{server_id}?msg={quote(message)}", status_code=303
    )


@router.get("/{server_id}/properties", response_class=HTMLResponse)
def properties_form(server_id: int, request: Request, db: Session = Depends(get_db)):
    server = _get_server_or_404(db, server_id)
    props, exists = properties_service.read_properties(server)
    return templates.TemplateResponse(
        request,
        "properties.html",
        {
            "title": f"{server.name} · properties",
            "server": server,
            "props": props,
            "exists": exists,
        },
    )


@router.post("/{server_id}/properties")
async def properties_save(server_id: int, request: Request, db: Session = Depends(get_db)):
    server = _get_server_or_404(db, server_id)
    form = await request.form()

    # Only persist keys that already exist in the file; ignore unknown input.
    existing, exists = properties_service.read_properties(server)
    if not exists:
        return _action_redirect(server_id, "server.properties not found; nothing saved.")

    new_values = dict(existing)
    for key in existing:
        field = f"prop__{key}"
        if field in form:
            new_values[key] = str(form[field])

    properties_service.write_properties(server, new_values)
    from urllib.parse import quote

    return RedirectResponse(
        url=f"/servers/{server_id}/properties?msg={quote('Saved (.bak created).')}",
        status_code=303,
    )


@router.post("/{server_id}/start")
def action_start(server_id: int, db: Session = Depends(get_db)):
    server = _get_server_or_404(db, server_id)
    # v0.6 backup-on-start: take a throttled snapshot before launch. If the
    # backup fails, abort the start (the world is left untouched, server stopped).
    gate = backup_service.maybe_backup_on_start(db, server)
    if not gate.allow_start:
        return _action_redirect(server_id, gate.message)
    try:
        msg = server_process.start_server(db, server)
    except FileNotFoundError as exc:
        msg = f"Start failed: {exc}"
    if gate.message:
        msg = f"{gate.message} {msg}"
    return _action_redirect(server_id, msg)


@router.post("/{server_id}/stop")
def action_stop(server_id: int, db: Session = Depends(get_db)):
    server = _get_server_or_404(db, server_id)
    msg = server_process.stop_server(db, server)
    return _action_redirect(server_id, msg)


@router.post("/{server_id}/restart")
def action_restart(server_id: int, db: Session = Depends(get_db)):
    server = _get_server_or_404(db, server_id)
    try:
        msg = server_process.restart_server(db, server)
    except FileNotFoundError as exc:
        msg = f"Restart failed: {exc}"
    return _action_redirect(server_id, msg)


@router.post("/{server_id}/kill")
def action_kill(server_id: int, db: Session = Depends(get_db)):
    """Force-kill (SIGKILL) — the explicit, operator-confirmed last resort.

    This is the ONLY path that ever sends SIGKILL, and it is never automatic:
    the UI guards it behind a confirmation dialog. A normal Stop still escalates
    only as far as SIGTERM (see ``server_process.stop_server``).
    """
    server = _get_server_or_404(db, server_id)
    msg = server_process.kill_server(db, server)
    return _action_redirect(server_id, msg)


@router.post("/{server_id}/java")
def action_set_java(
    server_id: int, java_path: str = Form(...), db: Session = Depends(get_db)
):
    """Set this server's Java runtime to one of the host's detected JREs.

    Only a path we actually detected is accepted — the value is fed to
    ``subprocess.Popen`` at launch, so an operator can never point a server at an
    arbitrary binary. The system-wide ``/usr/bin/java`` alternatives link is left
    untouched; only this server's ``java_path`` changes.
    """
    server = _get_server_or_404(db, server_id)
    chosen = (java_path or "").strip()
    runtimes = java_runtime.detect_runtimes()
    rt = java_runtime.find_runtime(chosen, runtimes)
    if rt is None:
        return _action_redirect(
            server_id,
            "Java runtime not changed: that path is not one of the detected "
            "runtimes. Pick one from the list.",
        )

    server.java_path = chosen
    # Keep the displayed start command in step with the new runtime.
    server.start_command = " ".join(server_process.build_start_command(server))
    db.commit()

    note = ""
    if server.status == "running":
        note = " The change applies the next time the server is started."
    label = f"Java {rt['major']}" if rt.get("major") else "the selected runtime"
    return _action_redirect(
        server_id, f"Server Java runtime set to {label} ({chosen})." + note
    )


# ----- RCON (v0.2) ------------------------------------------------------------
#
# These routes are admin-only via the global AuthMiddleware (no extra dependency
# is needed). The test/command/generate-password endpoints return JSON for the
# in-page console; the settings endpoint is a regular form POST + redirect.


def _merge_properties(server: Server, updates: dict) -> None:
    """Apply ``updates`` to server.properties, preserving everything else.

    A ``.bak`` copy is made by ``properties_service.write_properties``; if the
    file does not exist yet it is created with just the given keys.
    """
    existing, _exists = properties_service.read_properties(server)
    merged = dict(existing)
    merged.update(updates)
    properties_service.write_properties(server, merged)


@router.post("/{server_id}/rcon/settings")
async def rcon_settings(server_id: int, request: Request, db: Session = Depends(get_db)):
    server = _get_server_or_404(db, server_id)
    form = await request.form()

    enabled = (form.get("rcon_enabled") or "").lower() in ("on", "true", "1", "yes")
    host = (form.get("rcon_host") or DEFAULT_RCON_HOST).strip() or DEFAULT_RCON_HOST
    port_raw = (form.get("rcon_port") or str(DEFAULT_RCON_PORT)).strip()
    # Blank password means "keep the current one" — we never round-trip the
    # stored password through the form.
    password = (form.get("rcon_password") or "").strip()

    try:
        port = int(port_raw)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        return _action_redirect(server_id, "RCON port must be an integer between 1 and 65535.")

    # Mirror the connection settings into the DB.
    server.rcon_enabled = enabled
    server.rcon_host = host
    server.rcon_port = port
    if password:
        server.rcon_password = password

    # Write the matching keys into server.properties (.bak created on the way).
    prop_updates = {
        "enable-rcon": "true" if enabled else "false",
        "rcon.port": str(port),
    }
    if password:
        prop_updates["rcon.password"] = password

    note = ""
    try:
        _merge_properties(server, prop_updates)
    except (ValueError, OSError) as exc:
        note = f" (warning: could not update server.properties: {exc})"

    db.commit()
    return _action_redirect(
        server_id,
        "RCON settings saved." + note
        + " Restart the Minecraft server for the changes to take effect.",
    )


@router.post("/{server_id}/rcon/generate-password")
def rcon_generate_password(server_id: int, db: Session = Depends(get_db)):
    """Return a fresh random password for the settings form to fill in.

    The password is NOT persisted here — the admin reviews it and saves it via
    the settings form. This keeps it out of redirect URLs and server logs.
    """
    _get_server_or_404(db, server_id)
    return JSONResponse({"ok": True, "password": rcon_service.generate_password()})


@router.post("/{server_id}/rcon/test")
def rcon_test(server_id: int, db: Session = Depends(get_db)):
    server = _get_server_or_404(db, server_id)
    result = rcon_service.test_connection(server)
    server.rcon_last_status = (result["message"] or "")[:250]
    db.commit()
    return JSONResponse(result)


@router.post("/{server_id}/rcon/command")
def rcon_command(server_id: int, command: str = Form(...), db: Session = Depends(get_db)):
    server = _get_server_or_404(db, server_id)
    result = rcon_service.run_command(server, command)
    server.rcon_last_status = (result["message"] or "")[:250]
    db.commit()
    return JSONResponse(result)


# ----- Mods / Plugins (v0.3) --------------------------------------------------
#
# Both kinds share the same handlers; ``kind`` is constrained by the service to
# {"mods", "plugins"}. All routes are admin-only via the global AuthMiddleware.


@router.post("/{server_id}/{kind}/upload")
async def mod_upload(
    server_id: int,
    kind: str,
    jar_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    server = _get_server_or_404(db, server_id)
    try:
        stored = await mod_service.save_upload(server, kind, jar_file)
        msg = f"Uploaded {stored} to {kind}/."
    except mod_service.ModError as exc:
        msg = f"Upload failed: {exc}"
    return RedirectResponse(
        url=f"/servers/{server_id}?msg={quote(msg)}#{kind}", status_code=303
    )


@router.post("/{server_id}/{kind}/toggle")
def mod_toggle(
    server_id: int,
    kind: str,
    name: str = Form(...),
    db: Session = Depends(get_db),
):
    server = _get_server_or_404(db, server_id)
    try:
        msg = mod_service.toggle(server, kind, name)
    except mod_service.ModError as exc:
        msg = f"Toggle failed: {exc}"
    return RedirectResponse(
        url=f"/servers/{server_id}?msg={quote(msg)}#{kind}", status_code=303
    )


# ----- Backup schedule (v0.3) -------------------------------------------------


@router.post("/{server_id}/schedule")
def save_schedule(
    server_id: int,
    enabled: Optional[str] = Form(None),
    schedule_type: str = Form(SCHEDULE_DAILY),
    time_of_day: str = Form("03:30"),
    interval_minutes: str = Form("60"),
    retention_count: str = Form(str(DEFAULT_RETENTION_COUNT)),
    db: Session = Depends(get_db),
):
    server = _get_server_or_404(db, server_id)
    enabled_b = (enabled or "").lower() in ("on", "true", "1", "yes")

    if schedule_type not in (SCHEDULE_DAILY, SCHEDULE_INTERVAL):
        schedule_type = SCHEDULE_DAILY

    # Validate the field relevant to the chosen type; fall back to safe values.
    tod = (time_of_day or "").strip()
    if schedule_type == SCHEDULE_DAILY:
        parts = tod.split(":")
        valid = (
            len(parts) == 2
            and parts[0].isdigit()
            and parts[1].isdigit()
            and 0 <= int(parts[0]) <= 23
            and 0 <= int(parts[1]) <= 59
        )
        if not valid:
            return _action_redirect(server_id, "Schedule time must be HH:MM (24-hour).")

    try:
        interval_i = int(interval_minutes)
    except (TypeError, ValueError):
        interval_i = 0
    if schedule_type == SCHEDULE_INTERVAL and interval_i < 1:
        return _action_redirect(server_id, "Interval (minutes) must be 1 or more.")

    try:
        retention_i = max(1, int(retention_count))
    except (TypeError, ValueError):
        retention_i = DEFAULT_RETENTION_COUNT

    schedule = (
        db.query(BackupSchedule)
        .filter(BackupSchedule.server_id == server.id)
        .order_by(BackupSchedule.id.desc())
        .first()
    )
    if schedule is None:
        schedule = BackupSchedule(server_id=server.id)
        db.add(schedule)

    schedule.enabled = enabled_b
    schedule.schedule_type = schedule_type
    schedule.time_of_day = tod if schedule_type == SCHEDULE_DAILY else None
    schedule.interval_minutes = interval_i if schedule_type == SCHEDULE_INTERVAL else None
    schedule.retention_count = retention_i
    db.commit()

    state = "enabled" if enabled_b else "disabled"
    detail = (
        f"daily at {schedule.time_of_day}"
        if schedule_type == SCHEDULE_DAILY
        else f"every {schedule.interval_minutes} min"
    )
    return RedirectResponse(
        url=f"/servers/{server_id}?msg="
        + quote(f"Backup schedule saved ({state}, {detail}).")
        + "#schedule",
        status_code=303,
    )


# ----- Backup / restore hardening (v0.6) --------------------------------------
#
# v0.6 rsync hardlink snapshots with stop-first consistency, per-server settings,
# generation retention and restore. All routes are admin-only via AuthMiddleware.
# Path layout is /{server_id}/backup/... so it never collides with the
# /{server_id}/{kind}/upload|toggle mod/plugin routes.


def _get_backup_or_404(db: Session, server: Server, backup_id: int) -> Backup:
    backup = (
        db.query(Backup)
        .filter(Backup.id == backup_id, Backup.server_id == server.id)
        .first()
    )
    if backup is None:
        raise HTTPException(status_code=404, detail="Backup not found")
    return backup


@router.post("/{server_id}/backup/settings")
def backup_settings(
    server_id: int,
    backup_enabled: Optional[str] = Form(None),
    backup_time: str = Form("04:00"),
    backup_on_start_enabled: Optional[str] = Form(None),
    backup_on_start_min_interval_hours: str = Form("6"),
    backup_keep_generations: str = Form("7"),
    db: Session = Depends(get_db),
):
    server = _get_server_or_404(db, server_id)

    def _checked(value: Optional[str]) -> bool:
        return (value or "").lower() in ("on", "true", "1", "yes")

    # Validate HH:MM (24h); reject bad input rather than store garbage.
    time_str = (backup_time or "").strip()
    parts = time_str.split(":")
    valid_time = (
        len(parts) == 2
        and parts[0].isdigit()
        and parts[1].isdigit()
        and 0 <= int(parts[0]) <= 23
        and 0 <= int(parts[1]) <= 59
    )
    if not valid_time:
        return _action_redirect(server_id, "Backup time must be HH:MM (24-hour). Not saved.")

    try:
        interval_i = max(0, int(backup_on_start_min_interval_hours))
    except (TypeError, ValueError):
        interval_i = 6
    try:
        keep_i = max(1, int(backup_keep_generations))
    except (TypeError, ValueError):
        keep_i = 7

    server.backup_enabled = _checked(backup_enabled)
    server.backup_time = time_str
    server.backup_on_start_enabled = _checked(backup_on_start_enabled)
    server.backup_on_start_min_interval_hours = interval_i
    server.backup_keep_generations = keep_i
    db.commit()
    return RedirectResponse(
        url=f"/servers/{server_id}?msg={quote('Backup settings saved.')}#backups",
        status_code=303,
    )


@router.post("/{server_id}/backup/run")
def backup_run(server_id: int, db: Session = Depends(get_db)):
    """Take a stop-aware rsync snapshot now (stops a running server first)."""
    server = _get_server_or_404(db, server_id)
    outcome = backup_service.backup_now(db, server)
    return RedirectResponse(
        url=f"/servers/{server_id}?msg={quote(outcome.message)}#backups",
        status_code=303,
    )


@router.post("/{server_id}/backup/{backup_id}/restore")
def backup_restore(server_id: int, backup_id: int, db: Session = Depends(get_db)):
    server = _get_server_or_404(db, server_id)
    backup = _get_backup_or_404(db, server, backup_id)
    outcome = backup_service.restore_backup(db, server, backup)
    return RedirectResponse(
        url=f"/servers/{server_id}?msg={quote(outcome.message)}#backups",
        status_code=303,
    )


@router.post("/{server_id}/backup/{backup_id}/delete")
def backup_delete(server_id: int, backup_id: int, db: Session = Depends(get_db)):
    server = _get_server_or_404(db, server_id)
    backup = _get_backup_or_404(db, server, backup_id)
    name = backup.backup_name
    ok = backup_service.delete_backup(db, server, backup)
    msg = f"Backup {name} deleted." if ok else "Could not delete backup."
    return RedirectResponse(
        url=f"/servers/{server_id}?msg={quote(msg)}#backups",
        status_code=303,
    )
