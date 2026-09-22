"""Manual speedtest run routes."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse


def build_manual_runs_router(
    *,
    require_session: Callable[[Request], dict],
    require_csrf: Callable[[Request, dict], None],
    env_int: Callable[[str, int], int],
    get_last_manual_speedtest_at: Callable[[], float],
    set_last_manual_speedtest_at: Callable[[float], None],
    manual_run_snapshot: Callable[[], dict],
    load_speedtest_completion_state: Callable[[], dict],
    iso_from_epoch: Callable[[float | int | None], str | None],
    load_config: Callable[[], dict],
    resolve_server_label: Callable[[str, dict | None], str],
    try_acquire_manual_speedtest_lock: Callable[[], bool],
    release_manual_speedtest_lock: Callable[[], None],
    start_manual_run_state: Callable[[str, str], None],
    start_manual_speedtest_thread: Callable[[str], None],
    update_manual_run_state: Callable[..., None],
    iso_now: Callable[[], str],
    logger: logging.Logger,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/run/speedtest/status")
    def speedtest_run_status(request: Request) -> JSONResponse:
        require_session(request)
        cooldown_seconds = env_int("MANUAL_SPEEDTEST_COOLDOWN_SECONDS", 0)
        remaining = max(0, int((get_last_manual_speedtest_at() + cooldown_seconds) - time.time()))

        payload = manual_run_snapshot()
        payload["cooldown_remaining_seconds"] = remaining
        return JSONResponse(payload)

    @router.get("/api/run/speedtest/completion")
    def speedtest_completion_status(request: Request) -> JSONResponse:
        require_session(request)
        state = load_speedtest_completion_state()
        return JSONResponse(
            {
                "sequence": int(state.get("sequence", 0)),
                "status": str(state.get("status", "unknown")),
                "source": str(state.get("source", "unknown")),
                "completed_at": iso_from_epoch(state.get("completed_at")),
                "updated_at": iso_from_epoch(state.get("updated_at")),
            }
        )

    @router.post("/api/run/speedtest")
    async def run_speedtest_now(request: Request) -> JSONResponse:
        session = require_session(request)
        require_csrf(request, session)

        payload: dict = {}
        if request.headers.get("content-type", "").startswith("application/json"):
            payload = await request.json()

        selected_id = str(payload.get("server_id", "") or "").strip()
        if selected_id and not selected_id.isdigit():
            raise HTTPException(status_code=400, detail="server_id must be numeric or empty")

        config = load_config()
        selected_label = resolve_server_label(selected_id, config)

        cooldown_seconds = env_int("MANUAL_SPEEDTEST_COOLDOWN_SECONDS", 0)
        now = time.time()
        remaining = int((get_last_manual_speedtest_at() + cooldown_seconds) - now)
        if remaining > 0:
            return JSONResponse(
                {
                    "status": "cooldown",
                    "message": f"Manual speed test cooldown active. Retry in {remaining}s.",
                    "cooldown_remaining_seconds": remaining,
                },
                status_code=429,
            )

        if not try_acquire_manual_speedtest_lock():
            current_payload = manual_run_snapshot()
            current_payload["message"] = current_payload.get("message") or "A speed test is already running."
            return JSONResponse(current_payload, status_code=409)

        set_last_manual_speedtest_at(now)
        start_manual_run_state(selected_id, selected_label)

        try:
            start_manual_speedtest_thread(selected_id)
        except Exception:
            release_manual_speedtest_lock()
            update_manual_run_state(
                status="failed",
                stage="Failed",
                message="Unable to start manual speed test worker.",
                completed_at=iso_now(),
                exit_code=-1,
            )
            logger.exception("Failed to start manual speed test worker")
            return JSONResponse(manual_run_snapshot(), status_code=500)

        current_payload = manual_run_snapshot()
        current_payload["message"] = "Manual speed test started."
        return JSONResponse(current_payload, status_code=202)

    return router
