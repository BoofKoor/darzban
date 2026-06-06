import asyncio
import json
import os
import shutil
import tempfile
import time

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from starlette.websockets import WebSocketDisconnect

from app import logger, xray
from app.db import Session, get_db
from app.models.admin import Admin
from app.models.core import CoreStats
from app.utils import responses
from app.utils.jsonc import load_commented_json
from app.xray import XRayConfig
from app.xray.core import XRayConfigError
from config import XRAY_JSON

router = APIRouter(tags=["Core"], prefix="/api", responses={401: responses._401})


def _atomic_write(path: str, text: str) -> None:
    """Write `text` to `path` atomically (temp file in the same dir + replace).

    Prevents a crash/full-disk mid-write from leaving a half-written, corrupt
    `xray_config.json` — which would brick the next boot's config load.
    """
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".xray_config.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@router.websocket("/core/logs")
async def core_logs(websocket: WebSocket, db: Session = Depends(get_db)):
    token = websocket.query_params.get("token") or websocket.headers.get(
        "Authorization", ""
    ).removeprefix("Bearer ")
    admin = Admin.get_admin(token, db)
    if not admin:
        return await websocket.close(reason="Unauthorized", code=4401)

    if not admin.is_sudo:
        return await websocket.close(reason="You're not allowed", code=4403)

    interval = websocket.query_params.get("interval")
    if interval:
        try:
            interval = float(interval)
        except ValueError:
            return await websocket.close(reason="Invalid interval value", code=4400)
        if interval > 10:
            return await websocket.close(
                reason="Interval must be more than 0 and at most 10 seconds", code=4400
            )

    await websocket.accept()

    cache = ""
    last_sent_ts = 0
    with xray.core.get_logs() as logs:
        while True:
            if interval and time.time() - last_sent_ts >= interval and cache:
                try:
                    await websocket.send_text(cache)
                except (WebSocketDisconnect, RuntimeError):
                    break
                cache = ""
                last_sent_ts = time.time()

            if not logs:
                try:
                    await asyncio.wait_for(websocket.receive(), timeout=0.2)
                    continue
                except asyncio.TimeoutError:
                    continue
                except (WebSocketDisconnect, RuntimeError):
                    break

            log = logs.popleft()

            if interval:
                cache += f"{log}\n"
                continue

            try:
                await websocket.send_text(log)
            except (WebSocketDisconnect, RuntimeError):
                break


@router.get("/core", response_model=CoreStats)
def get_core_stats(admin: Admin = Depends(Admin.get_current)):
    """Retrieve core statistics such as version and uptime."""
    return CoreStats(
        version=xray.core.version,
        started=xray.core.started,
        logs_websocket=router.url_path_for("core_logs"),
    )


@router.post("/core/restart", responses={403: responses._403})
def restart_core(admin: Admin = Depends(Admin.check_sudo_admin)):
    """Restart the core and all connected nodes."""
    startup_config = xray.config.include_db_users()
    xray.core.restart(startup_config)

    for node_id, node in list(xray.nodes.items()):
        if node.connected:
            xray.operations.restart_node(node_id, startup_config)

    return {}


@router.get("/core/config", responses={403: responses._403})
def get_core_config(admin: Admin = Depends(Admin.check_sudo_admin)) -> dict:
    """Get the current core configuration."""
    with open(XRAY_JSON, "r") as f:
        config = load_commented_json(f.read())

    return config


@router.put("/core/config", responses={403: responses._403})
def modify_core_config(
    payload: dict, admin: Admin = Depends(Admin.check_sudo_admin)
) -> dict:
    """Modify the core configuration and restart the core.

    Order matters: validate -> `xray run -test` gate -> back up -> persist
    atomically -> swap in-memory -> restart. Nothing is mutated until the gate
    passes, and the file is written before the in-memory swap so a write
    failure can't leave memory ahead of disk. If the core fails to start
    despite passing the gate, both the in-memory config and the file are
    rolled back to the previous known-good state.
    """
    try:
        config = XRayConfig(payload, api_port=xray.config.api_port)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))

    # Resolve to exactly what the core will run, then gate it through Xray's
    # own loader. A rejected config changes nothing — the panel stays up.
    startup_config = config.include_db_users()
    try:
        xray.core.test_config(startup_config)
    except XRayConfigError as err:
        raise HTTPException(
            status_code=422,
            detail=f"Xray rejected the configuration:\n{err.stderr}",
        )

    # Snapshot the last-known-good state for rollback.
    prev_config = xray.config
    bak_path = XRAY_JSON + ".bak"
    try:
        if os.path.exists(XRAY_JSON):
            shutil.copy2(XRAY_JSON, bak_path)
        else:
            bak_path = None
    except OSError:
        logger.warning("Could not back up xray_config.json; rollback disabled for this apply")
        bak_path = None

    # Persist before the in-memory swap (atomic, so a failed write never
    # corrupts the live file and never leaves memory ahead of disk).
    try:
        _atomic_write(XRAY_JSON, json.dumps(payload, indent=4))
    except OSError as err:
        raise HTTPException(status_code=500, detail=f"Failed to write config: {err}")

    xray.config = config

    try:
        xray.core.restart(startup_config)
    except Exception as err:
        # Passed -test but failed to start: restore previous config + file.
        logger.error("Applied config failed to start; rolling back", exc_info=True)
        xray.config = prev_config
        if bak_path and os.path.exists(bak_path):
            try:
                os.replace(bak_path, XRAY_JSON)
            except OSError:
                logger.error("Failed to restore xray_config.json from backup", exc_info=True)
        try:
            xray.core.restart(prev_config.include_db_users())
        except Exception:
            logger.error("Rollback restart of previous config also failed", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Applied config failed to start; rolled back to previous config. ({err})",
        )

    for node_id, node in list(xray.nodes.items()):
        if node.connected:
            xray.operations.restart_node(node_id, startup_config)

    xray.hosts.update()

    return payload
