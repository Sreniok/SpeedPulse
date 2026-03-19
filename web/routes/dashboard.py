"""Dashboard page and reporting routes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates


def build_dashboard_router(
    *,
    templates: Jinja2Templates,
    current_session: Callable[[Request], dict | None],
    load_config: Callable[[], dict],
    detected_account_network_identity: Callable[[dict], dict[str, str]],
    github_project_url: Callable[[dict | None], str],
    github_sponsors_url: Callable[[dict | None], str],
    ui_theme_preferences: Callable[[dict | None], dict[str, str]],
    require_session: Callable[[Request], dict],
    build_dashboard_payload_fn: Callable[[int, str], dict],
    load_measurement_entries_fn: Callable[[dict], list[dict]],
    filter_entries_for_mode_fn: Callable[[list[dict], datetime, int, str], list[dict]],
    clean_theme_id_fn: Callable[[object, str], str],
    resolve_report_theme_id_fn: Callable[[dict], str],
    build_report_html_fn: Callable[..., str],
) -> APIRouter:
    router = APIRouter()

    def render_account_page(request: Request, template_name: str) -> Response:
        session = current_session(request)
        if not session:
            return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

        config = load_config()
        account = config.get("account", {})
        detected_identity = detected_account_network_identity(config)

        response = templates.TemplateResponse(
            request,
            template_name,
            {
                "request": request,
                "login_email": session["login_email"],
                "account_name": account.get("name", "N/A"),
                "account_number": account.get("number", "N/A"),
                "account_provider": detected_identity["provider"],
                "account_ip_address": detected_identity["ip_address"],
                "csrf_token": session["csrf"],
                "github_url": github_project_url(config),
                "github_sponsors_url": github_sponsors_url(config),
                "ui_theme": ui_theme_preferences(config),
            },
        )
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @router.get("/", response_class=HTMLResponse)
    def dashboard_page(request: Request) -> Response:
        return render_account_page(request, "dashboard.html")

    @router.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request) -> Response:
        return render_account_page(request, "settings.html")

    @router.get("/api/metrics")
    def metrics(request: Request, days: int = 30, mode: str = "days") -> JSONResponse:
        require_session(request)
        if mode not in {"days", "today"}:
            raise HTTPException(status_code=400, detail="mode must be 'days' or 'today'")
        if mode == "days" and (days < 1 or days > 365):
            raise HTTPException(status_code=400, detail="days must be between 1 and 365")

        payload = build_dashboard_payload_fn(days, mode)
        return JSONResponse(payload)

    @router.get("/api/reports/download")
    def download_range_report(
        request: Request,
        mode: str = "today",
        days: int = 30,
        format: str = "html",
        theme_id: str = "",
    ) -> Response:
        require_session(request)

        report_format = str(format or "html").strip().lower()
        if report_format != "html":
            raise HTTPException(status_code=400, detail="Only HTML export is enabled right now.")

        if mode not in {"days", "today"}:
            raise HTTPException(status_code=400, detail="mode must be 'days' or 'today'")
        if mode == "days" and (days < 1 or days > 365):
            raise HTTPException(status_code=400, detail="days must be between 1 and 365")

        config = load_config()
        now = datetime.now()
        all_entries = load_measurement_entries_fn(config)
        selected_entries = filter_entries_for_mode_fn(all_entries, now, days, mode)

        if mode == "today":
            yesterday = now - timedelta(days=1)
            previous_entries = [entry for entry in all_entries if entry["timestamp"].date() == yesterday.date()]
            range_label = "Today"
            range_slug = "today"
        else:
            previous_start = now - timedelta(days=days * 2)
            previous_end = now - timedelta(days=days)
            previous_entries = [
                entry for entry in all_entries if previous_start <= entry["timestamp"] < previous_end
            ]
            range_label = f"Last {days} days"
            range_slug = f"{days}d"

        resolved_theme = clean_theme_id_fn(theme_id, resolve_report_theme_id_fn(config))
        report_html = build_report_html_fn(
            config,
            selected_entries,
            report_title="SpeedPulse Performance Report",
            range_label=range_label,
            theme_id=resolved_theme,
            previous_entries=previous_entries,
        )
        filename = f"speedpulse-report-{range_slug}-{now.strftime('%Y%m%d-%H%M')}.html"

        return Response(
            content=report_html,
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename=\"{filename}\"'},
        )

    return router
