"""System and health-related routes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from measurement_store import list_notification_events


def build_system_router(
    *,
    logo_path: Path,
    version: str,
    require_session: Callable[[Request], dict],
    build_readiness_state: Callable[[], tuple[list[str], list[str], dict[str, str]]],
) -> APIRouter:
    router = APIRouter()

    @router.get("/logo.svg", include_in_schema=False)
    def app_logo() -> FileResponse:
        if not logo_path.is_file():
            raise HTTPException(status_code=404, detail="Logo not found.")
        response = FileResponse(logo_path, media_type="image/svg+xml")
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @router.get("/api/notifications/log")
    async def api_notification_log(request: Request):
        require_session(request)
        entries = list_notification_events(limit=50)
        if not entries:
            from state_store import get_notification_log

            entries = get_notification_log(limit=50)
        return entries

    @router.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "speedpulse-dashboard",
            "version": version,
            "time": datetime.now().isoformat(),
        }

    @router.get("/ready")
    def readiness() -> JSONResponse:
        failures, warnings, checks = build_readiness_state()
        payload: dict[str, object] = {
            "status": "ready" if not failures else "not_ready",
            "service": "speedpulse-dashboard",
            "version": version,
            "time": datetime.now().isoformat(),
            "checks": checks,
            "warnings": warnings,
        }
        if failures:
            payload["failures"] = failures
            return JSONResponse(payload, status_code=503)
        return JSONResponse(payload)

    return router
