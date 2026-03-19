"""Backup and restore routes."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

MAX_BACKUP_UPLOAD_BYTES = 100 * 1024 * 1024


def build_backup_router(
    *,
    require_session: Callable[[Request], dict],
    require_csrf: Callable[[Request, dict], None],
    load_config: Callable[[], dict],
    logger: logging.Logger,
    create_backup_fn: Callable[[str, bool], tuple[bytes, str]],
    delete_backup_fn: Callable[[str, dict], bool],
    get_backup_path_fn: Callable[[str, dict], Path | None],
    list_backups_fn: Callable[[dict], list[dict[str, object]]],
    restore_backup_fn: Callable[[bytes, str], dict[str, object]],
    save_backup_to_path_fn: Callable[[bytes, str, dict], Path],
    validate_backup_fn: Callable[[bytes, str], dict[str, object]],
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/backup/create")
    async def api_backup_create(request: Request):
        session = require_session(request)
        require_csrf(request, session)

        body = await request.json()
        entered_password = str(body.get("password", "")).strip()
        stored_password = os.getenv("BACKUP_PASSWORD", "").strip()
        password = entered_password or stored_password
        include_logs = bool(body.get("include_logs", True))
        download = bool(body.get("download", False))

        if entered_password and len(entered_password) < 6:
            raise HTTPException(status_code=400, detail="Backup password must be at least 6 characters.")
        if not password or len(password) < 6:
            raise HTTPException(
                status_code=400,
                detail="Enter a backup password, or save one first in Scheduled backups.",
            )

        config = load_config()
        encrypted, filename = create_backup_fn(password, include_logs)
        try:
            dest = save_backup_to_path_fn(encrypted, filename, config)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Backup created but could not be saved to disk: {exc}",
            ) from exc

        if not download:
            return JSONResponse(
                {
                    "message": "Backup saved to the configured backup directory.",
                    "filename": dest.name,
                    "size_bytes": len(encrypted),
                }
            )

        return Response(
            content=encrypted,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/api/backup/list")
    async def api_backup_list(request: Request):
        require_session(request)
        config = load_config()
        return JSONResponse({"backups": list_backups_fn(config)})

    @router.get("/api/backup/download/{filename}")
    async def api_backup_download(request: Request, filename: str):
        require_session(request)
        config = load_config()
        path = get_backup_path_fn(filename, config)
        if path is None:
            raise HTTPException(status_code=404, detail="Backup not found.")
        data = path.read_bytes()
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.post("/api/backup/preview")
    async def api_backup_preview(request: Request):
        session = require_session(request)
        require_csrf(request, session)

        form = await request.form()
        password = str(form.get("password", "")).strip()
        upload = form.get("file")

        if not password:
            raise HTTPException(status_code=400, detail="Backup password is required.")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="No backup file uploaded.")

        data = await upload.read()
        if len(data) > MAX_BACKUP_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="Backup file is too large (max 100 MB).")

        try:
            manifest = validate_backup_fn(data, password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return JSONResponse({"manifest": manifest})

    @router.post("/api/backup/restore")
    async def api_backup_restore(request: Request):
        session = require_session(request)
        require_csrf(request, session)

        form = await request.form()
        password = str(form.get("password", "")).strip()
        upload = form.get("file")

        if not password:
            raise HTTPException(status_code=400, detail="Backup password is required.")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="No backup file uploaded.")

        data = await upload.read()
        if len(data) > MAX_BACKUP_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="Backup file is too large (max 100 MB).")

        try:
            summary = restore_backup_fn(data, password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        logger.info("Backup restored: %s", summary.get("restored", []))
        return JSONResponse(
            {
                "message": "Backup restored successfully.",
                "restored": summary.get("restored", []),
                "warnings": summary.get("warnings", []),
                "restart_required": True,
            }
        )

    @router.delete("/api/backup/{filename}")
    async def api_backup_delete(request: Request, filename: str):
        session = require_session(request)
        require_csrf(request, session)

        config = load_config()
        if not delete_backup_fn(filename, config):
            raise HTTPException(status_code=404, detail="Backup not found.")
        return JSONResponse({"message": "Backup deleted."})

    return router
