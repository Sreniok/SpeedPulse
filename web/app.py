#!/usr/bin/env python3
"""SpeedPulse – FastAPI dashboard with session-based login."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import smtplib
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

from backup_manager import (
    create_backup,
    delete_backup,
    get_backup_path,
    list_backups,
    restore_backup,
    save_backup_to_path,
    validate_backup,
)
from mail_settings import load_mail_settings
from measurement_repository import load_measurement_entries, load_measurement_entries_in_range
from measurement_store import (
    database_enabled,
    database_healthcheck,
    encrypted_secret_store_enabled,
    has_app_secret,
    set_app_secret,
)
from reporting import build_contract_report_html, build_report_html, resolve_report_theme_id
from state_store import (
    blocked_seconds as state_blocked_seconds,
)
from state_store import (
    bump_session_version,
    get_session_version,
    get_state_db_path,
    initialize_state_store,
    load_manual_runtime_state,
    load_speedtest_completion_state,
    save_manual_runtime_state,
    store_reset_token,
)
from state_store import (
    clear_login_failures as state_clear_login_failures,
)
from state_store import (
    consume_reset_token as state_consume_reset_token,
)
from state_store import (
    register_failed_login as state_register_failed_login,
)
from version import USER_AGENT, __version__
from web.routes.auth import build_auth_router
from web.routes.backups import build_backup_router
from web.routes.dashboard import build_dashboard_router
from web.routes.manual_runs import build_manual_runs_router
from web.routes.system import build_system_router
from web.services.system import build_readiness_state
from web.services.system import resolve_speedtest_executable as service_resolve_speedtest_executable
from web.services.system import server_setting_payload as build_server_setting_payload

SCRIPT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
ENV_PATH = SCRIPT_DIR / ".env"
LOGO_PATH = Path(__file__).parent / "static" / "logo2.svg"
APP_CSS_PATH = Path(__file__).parent / "static" / "app.css"
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.globals["app_version"] = __version__
_LOGO_VERSION = str(int(LOGO_PATH.stat().st_mtime_ns)) if LOGO_PATH.is_file() else __version__
TEMPLATES.env.globals["logo_version"] = _LOGO_VERSION
_STATIC_VERSION = str(int(APP_CSS_PATH.stat().st_mtime_ns)) if APP_CSS_PATH.is_file() else __version__
TEMPLATES.env.globals["static_version"] = _STATIC_VERSION

LOGGER = logging.getLogger("speedpulse.web")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SESSION_COOKIE = "speedtest_session"
FLASH_COOKIE = "speedtest_flash"
AUTH_SALT = os.getenv("AUTH_SALT", "")


def _is_secure_request(request: Request) -> bool:
    """Resolve the Secure flag for session cookies.

    * ``true``  → always Secure (HTTPS deployments).
    * ``false`` → never Secure (plain HTTP / dev).
    * ``auto`` or unset → detect from the request scheme /
      ``X-Forwarded-Proto`` header.
    """
    setting = os.getenv("SESSION_COOKIE_SECURE", "auto").strip().lower()
    if setting == "true":
        return True
    if setting == "false":
        return False
    # auto-detect
    if request.url.scheme == "https":
        return True
    if (request.headers.get("x-forwarded-proto") or "").lower() == "https":
        return True
    return False

SESSION_VERSION = 1
SESSION_VERSION_LOCK = threading.Lock()

MANUAL_SPEEDTEST_LOCK = threading.Lock()
LAST_MANUAL_SPEEDTEST_AT = 0.0
MANUAL_RUN_STATE_LOCK = threading.Lock()
DEFAULT_MANUAL_RUN_STATE = {
    "status": "idle",
    "stage": "Idle",
    "message": "",
    "logs": [],
    "selected_server_id": "",
    "selected_server_label": "Auto (nearest server)",
    "started_at": None,
    "completed_at": None,
    "updated_at": None,
    "exit_code": None,
}
MANUAL_RUN_STATE = dict(DEFAULT_MANUAL_RUN_STATE)
SERVER_OPTIONS_CACHE_TTL_SECONDS = 600
SERVER_OPTIONS_CACHE = {"fetched_at": 0.0, "options": []}
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PUSH_EVENT_DEFAULTS = {
    "alert": True,
    "weekly_report": True,
    "monthly_report": True,
    "health_check": True,
}


def _runtime_root() -> Path:
    raw_value = os.getenv("APP_DATA_DIR", "").strip()
    if not raw_value:
        return SCRIPT_DIR
    path = Path(raw_value)
    if path.is_absolute():
        return path
    return SCRIPT_DIR / path


def _resolve_runtime_path(default_path: Path, env_name: str) -> Path:
    raw_value = os.getenv(env_name, "").strip()
    if not raw_value:
        return default_path
    path = Path(raw_value)
    if path.is_absolute():
        return path
    return SCRIPT_DIR / path


def _config_path() -> Path:
    explicit_path = _resolve_runtime_path(CONFIG_PATH, "CONFIG_PATH")
    if explicit_path != CONFIG_PATH:
        return explicit_path
    data_root = os.getenv("APP_DATA_DIR", "").strip()
    if data_root:
        return _runtime_root() / CONFIG_PATH.name
    return CONFIG_PATH


def _env_path() -> Path:
    return _resolve_runtime_path(ENV_PATH, "ENV_PATH")


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _current_session_version() -> int:
    with SESSION_VERSION_LOCK:
        return SESSION_VERSION


def _rotate_session_version() -> int:
    global SESSION_VERSION
    with SESSION_VERSION_LOCK:
        SESSION_VERSION = bump_session_version()
        return SESSION_VERSION


def _get_last_manual_speedtest_at() -> float:
    return LAST_MANUAL_SPEEDTEST_AT


def _set_last_manual_speedtest_at(value: float) -> None:
    global LAST_MANUAL_SPEEDTEST_AT
    LAST_MANUAL_SPEEDTEST_AT = value


def _try_acquire_manual_speedtest_lock() -> bool:
    return MANUAL_SPEEDTEST_LOCK.acquire(blocking=False)


def _release_manual_speedtest_lock() -> None:
    if MANUAL_SPEEDTEST_LOCK.locked():
        MANUAL_SPEEDTEST_LOCK.release()


def _build_system_readiness_state() -> tuple[list[str], list[str], dict[str, str]]:
    return build_readiness_state(
        config_path=_config_path,
        load_config=load_config,
        runtime_root=_runtime_root,
        get_state_db_path=get_state_db_path,
        database_healthcheck=database_healthcheck,
        database_enabled=database_enabled,
        load_mail_settings=load_mail_settings,
        resolve_speedtest_executable_fn=resolve_speedtest_executable,
    )


def resolve_speedtest_executable(config: dict) -> str:
    return service_resolve_speedtest_executable(config)


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _iso_from_epoch(value: float | int | None) -> str | None:
    try:
        epoch = float(value or 0.0)
    except (TypeError, ValueError):
        return None
    if epoch <= 0:
        return None
    return datetime.fromtimestamp(epoch).isoformat(timespec="seconds")


def _manual_run_snapshot() -> dict:
    with MANUAL_RUN_STATE_LOCK:
        payload = dict(MANUAL_RUN_STATE)
        payload["logs"] = list(MANUAL_RUN_STATE.get("logs", []))
        return payload


def _persist_manual_runtime_state() -> None:
    snapshot = _manual_run_snapshot()
    save_manual_runtime_state(snapshot, LAST_MANUAL_SPEEDTEST_AT)


def _update_manual_run_state(**changes: object) -> None:
    with MANUAL_RUN_STATE_LOCK:
        MANUAL_RUN_STATE.update(changes)
        MANUAL_RUN_STATE["updated_at"] = _iso_now()
    _persist_manual_runtime_state()


def _append_manual_run_log(line: str) -> None:
    with MANUAL_RUN_STATE_LOCK:
        logs = list(MANUAL_RUN_STATE.get("logs", []))
        logs.append(line)
        MANUAL_RUN_STATE["logs"] = logs[-60:]
        MANUAL_RUN_STATE["updated_at"] = _iso_now()
    _persist_manual_runtime_state()


def _resolve_server_label(server_id: str, config: dict | None = None) -> str:
    selected_id = str(server_id or "").strip()
    if not selected_id:
        return "Auto (nearest server)"

    try:
        payload = server_setting_payload(config=config, force_refresh=False)
    except Exception:
        LOGGER.exception("Failed to resolve speedtest server label")
        return f"Pinned server #{selected_id}"

    for option in payload.get("options", []):
        if option.get("id") == selected_id:
            return option.get("label", f"Pinned server #{selected_id}")

    return f"Pinned server #{selected_id}"


def _start_manual_run_state(selected_server_id: str = "", selected_server_label: str = "Auto (nearest server)") -> None:
    now = _iso_now()
    with MANUAL_RUN_STATE_LOCK:
        MANUAL_RUN_STATE.update(
            {
                "status": "running",
                "stage": "Preparing test",
                "message": "Starting manual speed test...",
                "logs": [],
                "selected_server_id": selected_server_id,
                "selected_server_label": selected_server_label,
                "started_at": now,
                "completed_at": None,
                "updated_at": now,
                "exit_code": None,
            }
        )
    _persist_manual_runtime_state()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global SESSION_VERSION, LAST_MANUAL_SPEEDTEST_AT, MANUAL_RUN_STATE

    validate_security_configuration()
    initialize_state_store(DEFAULT_MANUAL_RUN_STATE)
    with SESSION_VERSION_LOCK:
        SESSION_VERSION = get_session_version()

    last_run_at, persisted_manual_state = load_manual_runtime_state(DEFAULT_MANUAL_RUN_STATE)
    with MANUAL_RUN_STATE_LOCK:
        MANUAL_RUN_STATE = dict(DEFAULT_MANUAL_RUN_STATE)
        MANUAL_RUN_STATE.update(persisted_manual_state or {})
        LAST_MANUAL_SPEEDTEST_AT = float(last_run_at or 0.0)

        if MANUAL_RUN_STATE.get("status") == "running":
            now = _iso_now()
            MANUAL_RUN_STATE.update(
                {
                    "status": "failed",
                    "stage": "Interrupted",
                    "message": "Manual speed test was interrupted by an application restart.",
                    "completed_at": now,
                    "updated_at": now,
                    "exit_code": -1,
                }
            )

    _persist_manual_runtime_state()
    LOGGER.info("Web security configuration validated")
    yield


APP = FastAPI(title="SpeedPulse Dashboard", version=__version__, lifespan=lifespan)
APP.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


def _infer_manual_run_stage(line: str) -> str | None:
    text = line.lower()

    if "preparing speedtest engine" in text:
        return "Preparing test"
    if "using " in text and "via" in text:
        return "Preparing speed test engine"
    if "selected server #" in text or "automatic server selection" in text:
        return "Selecting server"
    if "running" in text and "attempt" in text:
        return "Connecting to test server"
    if "connected to test server:" in text:
        return "Connecting to test server"
    if "idle latency:" in text:
        return "Measuring latency"
    if text.startswith("download:") and "%" in text:
        return "Measuring download speed"
    if text.startswith("upload:") and "%" in text:
        return "Measuring upload speed"
    if "measuring download and upload throughput" in text:
        return "Measuring line speed"
    if "waiting" in text and "before retry" in text:
        return "Waiting before retry"
    if "finished, validating result payload" in text:
        return "Reading measured results"
    if "test results" in text:
        return "Reading measured results"
    if "saving result to log" in text:
        return "Saving result to log"
    if "logged to:" in text:
        return "Saving result to log"
    if "evaluating alert thresholds" in text:
        return "Checking thresholds"
    if "rendering result summary" in text:
        return "Rendering summary"
    if "completed successfully" in text:
        return "Completed"
    if "timed out" in text:
        return "Timed out"
    if "failed" in text or "unexpected error" in text or "cancelled" in text:
        return "Failed"

    return None


def _manual_run_terminal_message(returncode: int, logs: list[str]) -> tuple[str, str]:
    combined = "\n".join(logs).lower()
    if returncode == 0:
        return ("Completed", "Speed test completed successfully.")
    if "timed out" in combined:
        return ("Timed out", "Speed test timed out.")
    return ("Failed", "Speed test failed. Check server logs for details.")


def _manual_speedtest_worker(selected_server_id: str = "") -> None:
    process = None
    try:
        _update_manual_run_state(stage="Launching speed test")
        child_env = os.environ.copy()
        child_env["PYTHONUNBUFFERED"] = "1"
        child_env["SPEEDTEST_RUN_SOURCE"] = "manual"
        if selected_server_id:
            child_env["SPEEDTEST_SERVER_ID"] = selected_server_id
        else:
            child_env.pop("SPEEDTEST_SERVER_ID", None)

        process = subprocess.Popen(
            [sys.executable, str(SCRIPT_DIR / "CheckSpeed.py")],
            cwd=SCRIPT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=child_env,
        )

        if process.stdout is not None:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue

                _append_manual_run_log(line)
                next_stage = _infer_manual_run_stage(line)
                if next_stage:
                    _update_manual_run_state(stage=next_stage, message=line)
                else:
                    _update_manual_run_state(message=line)

        returncode = process.wait()
        snapshot = _manual_run_snapshot()
        stage, message = _manual_run_terminal_message(returncode, snapshot.get("logs", []))
        _update_manual_run_state(
            status="completed" if returncode == 0 else "failed",
            stage=stage,
            message=message,
            completed_at=_iso_now(),
            exit_code=returncode,
        )
    except Exception:
        LOGGER.exception("Manual speed test worker crashed")
        _append_manual_run_log("Unexpected error while running manual speed test.")
        _update_manual_run_state(
            status="failed",
            stage="Failed",
            message="Manual speed test failed unexpectedly.",
            completed_at=_iso_now(),
            exit_code=-1,
        )
    finally:
        if process and process.stdout is not None:
            process.stdout.close()
        MANUAL_SPEEDTEST_LOCK.release()


def _start_manual_speedtest_thread(selected_server_id: str) -> None:
    worker = threading.Thread(
        target=_manual_speedtest_worker,
        kwargs={"selected_server_id": selected_server_id},
        name="manual-speedtest",
        daemon=True,
    )
    worker.start()


def run_weekly_report_now() -> tuple[bool, str, int]:
    script_path = SCRIPT_DIR / "SendWeeklyReport.py"
    if not script_path.exists():
        return (False, "Weekly report script is missing.", 500)

    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
    except Exception:
        LOGGER.exception("Failed to run manual weekly report")
        return (False, "Failed to start weekly report job.", 500)

    transcript = "\n".join(
        part.strip()
        for part in (result.stdout or "", result.stderr or "")
        if part and part.strip()
    ).strip()
    normalized = transcript.lower()

    if result.returncode == 0:
        return (True, "Weekly report email sent.", 200)

    if "no speed test data found" in normalized:
        return (False, "Weekly report was not sent because no data was found for the last completed week.", 409)

    if "failed to load mail settings" in normalized:
        return (False, "Weekly report failed: check SMTP settings in Settings.", 400)

    if transcript:
        last_line = transcript.splitlines()[-1].strip()
        return (False, f"Weekly report failed: {last_line}", 500)
    return (False, "Weekly report failed. Check scheduler logs for details.", 500)


def load_config() -> dict:
    with _config_path().open("r", encoding="utf-8") as handle:
        return json.load(handle)


CONFIG_LOCK = threading.Lock()
ENV_LOCK = threading.Lock()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    try:
        try:
            fd, raw_temp_path = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
            )
        except OSError:
            fd, raw_temp_path = tempfile.mkstemp(
                prefix=f"{path.name}.",
                suffix=".tmp",
            )

        temp_path = Path(raw_temp_path)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp_path, path)
        temp_path = None
        return
    except OSError:
        # Some read-only-container setups allow writing the bind-mounted file
        # itself but not creating sibling temp files. Fall back to a locked
        # in-place rewrite in that case.
        with path.open("r+", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            handle.write(content)
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def save_config(config: dict) -> None:
    with CONFIG_LOCK:
        rendered = json.dumps(config, indent=2) + "\n"
        _atomic_write_text(_config_path(), rendered)


def _clean_env_value(value: str) -> str:
    return str(value).replace("\n", "").replace("\r", "").strip()


def _update_env_file(updates: dict[str, str]) -> None:
    with ENV_LOCK:
        lines: list[str] = []
        env_path = _env_path()
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()

        # Track the FIRST position for each key; mark later duplicates for removal.
        first_position: dict[str, int] = {}
        duplicate_indices: set[int] = set()
        for index, raw_line in enumerate(lines):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#") or "=" not in raw_line:
                continue
            key = raw_line.split("=", 1)[0].strip()
            if not key:
                continue
            if key in first_position:
                duplicate_indices.add(index)
            else:
                first_position[key] = index

        # Remove duplicate lines (keep only the first occurrence of each key).
        if duplicate_indices:
            lines = [line for idx, line in enumerate(lines) if idx not in duplicate_indices]
            # Rebuild positions after removing duplicates.
            first_position.clear()
            for index, raw_line in enumerate(lines):
                stripped = raw_line.strip()
                if not stripped or stripped.startswith("#") or "=" not in raw_line:
                    continue
                key = raw_line.split("=", 1)[0].strip()
                if key and key not in first_position:
                    first_position[key] = index

        for key, value in updates.items():
            sanitized = _clean_env_value(value)
            escaped = sanitized.replace("\\", "\\\\").replace('"', '\\"')
            line = f'{key}="{escaped}"'
            if key in first_position:
                lines[first_position[key]] = line
            else:
                lines.append(line)

        rendered = "\n".join(lines).rstrip() + "\n"
        _atomic_write_text(env_path, rendered)


def _apply_runtime_env(updates: dict[str, str]) -> None:
    for key, value in updates.items():
        os.environ[key] = _clean_env_value(value)


def build_password_hash(password: str, iterations: int = 390000) -> str:
    salt_hex = secrets.token_hex(16)
    digest = hash_password_pbkdf2(password, salt_hex, iterations)
    return f"pbkdf2_sha256:{iterations}:{salt_hex}:{digest}"


def _normalize_email(value: object) -> str:
    return _clean_env_value(value).lower()


def _is_valid_email(value: object) -> bool:
    return bool(_EMAIL_PATTERN.fullmatch(_normalize_email(value)))


def _resolve_notification_email(config: dict | None = None) -> str:
    notification_email = _normalize_email(os.getenv("EMAIL_TO", ""))
    if notification_email:
        return notification_email

    loaded = config or load_config()
    email_cfg = loaded.get("email", {})
    return _normalize_email(email_cfg.get("to", ""))


def _resolve_login_email(config: dict | None = None) -> str:
    login_email = _normalize_email(os.getenv("DASHBOARD_LOGIN_EMAIL", ""))
    if _is_valid_email(login_email):
        return login_email

    legacy_username = _normalize_email(os.getenv("DASHBOARD_USERNAME", ""))
    if _is_valid_email(legacy_username):
        return legacy_username

    recovery_email = _normalize_email(os.getenv("RECOVERY_EMAIL", ""))
    if _is_valid_email(recovery_email):
        return recovery_email

    notification_email = _resolve_notification_email(config)
    if _is_valid_email(notification_email):
        return notification_email

    return ""


def _detected_account_network_identity(
    config: dict,
    entries: list[dict] | None = None,
) -> dict[str, str]:
    account_cfg = config.get("account", {})
    detected_provider = str(account_cfg.get("provider", "") or "").strip()
    detected_ip = str(account_cfg.get("ip_address", "") or "").strip()

    source_entries = entries
    if source_entries is None:
        source_entries = load_measurement_entries(config)

    if source_entries:
        latest = source_entries[-1]
        latest_provider = str(latest.get("isp", "") or "").strip()
        latest_ip = str(latest.get("ip_address", "") or "").strip()
        if latest_provider:
            detected_provider = latest_provider
        if latest_ip:
            detected_ip = latest_ip

    return {
        "provider": detected_provider,
        "ip_address": detected_ip,
    }


def dashboard_settings_payload(config: dict | None = None) -> dict:
    loaded = config or load_config()
    account_cfg = loaded.get("account", {})
    email_cfg = loaded.get("email", {})
    notifications_cfg = loaded.get("notifications", {})
    thresholds_cfg = loaded.get("thresholds", {})
    scheduling_cfg = loaded.get("scheduling", {})
    backup_cfg = loaded.get("backup", {})

    smtp_port_raw = os.getenv("SMTP_PORT", str(email_cfg.get("smtp_port", 465)))
    try:
        smtp_port = int(smtp_port_raw)
    except ValueError:
        smtp_port = 465

    smtp_username = os.getenv("SMTP_USERNAME", email_cfg.get("from", ""))
    email_from = os.getenv("EMAIL_FROM", email_cfg.get("from", smtp_username))
    smtp_password_set = has_app_secret("smtp_password") or bool(os.getenv("SMTP_PASSWORD", "").strip())

    contract_cfg = loaded.get("contract", {})
    current_contract = contract_cfg.get("current", {})
    contract_history = [
        _resolved_contract_entry(loaded, entry)
        for entry in contract_cfg.get("history", [])
    ]
    login_email = _resolve_login_email(loaded)
    notification_email = _resolve_notification_email(loaded)
    detected_identity = _detected_account_network_identity(loaded)
    push_events = _normalize_push_events(notifications_cfg.get("push_events", {}))
    report_theme_id = _clean_theme_id(notifications_cfg.get("report_theme_id", "default-dark"))
    ui_theme = _ui_theme_preferences(loaded)
    scan_enabled = bool(scheduling_cfg.get("scan_enabled", True))
    scan_frequency = _clean_scan_frequency(scheduling_cfg.get("scan_frequency", "daily"), "daily")
    scan_weekly_day = _clean_weekday_name(scheduling_cfg.get("scan_weekly_day", "Monday"), "Monday")
    scan_monthly_day = max(1, min(31, _safe_int(scheduling_cfg.get("scan_monthly_day", 1), 1)))
    scan_custom_days = sorted(
        {
            day
            for day in (_safe_int(value, 0) for value in scheduling_cfg.get("scan_custom_days", []))
            if 1 <= day <= 31
        }
    )
    if not scan_custom_days:
        scan_custom_days = [scan_monthly_day]
    app_timezone, app_timezone_source = _resolve_app_timezone(loaded)

    return {
        "login_email": login_email,
        "notification_email": notification_email,
        "username": login_email,
        "user_email": notification_email,
        "account": {
            "name": str(account_cfg.get("name", "")),
            "number": str(account_cfg.get("number", "")),
            "provider": detected_identity["provider"],
            "ip_address": detected_identity["ip_address"],
        },
        "server_selection_id": str(loaded.get("speedtest", {}).get("server_id", "") or ""),
        "email": {
            "smtp_server": os.getenv("SMTP_SERVER", email_cfg.get("smtp_server", "")),
            "smtp_port": smtp_port,
            "smtp_username": smtp_username,
            "smtp_password_set": smtp_password_set,
            "from": email_from,
            "to": os.getenv("EMAIL_TO", email_cfg.get("to", "")),
            "send_realtime_alerts": bool(email_cfg.get("send_realtime_alerts", True)),
        },
        "notifications": {
            "weekly_report_enabled": bool(notifications_cfg.get("weekly_report_enabled", True)),
            "weekly_report_time": scheduling_cfg.get("weekly_report_time", "Monday 08:00"),
            "monthly_report_enabled": bool(notifications_cfg.get("monthly_report_enabled", False)),
            "monthly_report_time": scheduling_cfg.get("monthly_report_time", "08:00"),
            "test_times": scheduling_cfg.get("test_times", ["08:00", "16:00", "22:00"]),
            "scan_enabled": scan_enabled,
            "scan_frequency": scan_frequency,
            "scan_weekly_day": scan_weekly_day,
            "scan_monthly_day": scan_monthly_day,
            "scan_custom_days": scan_custom_days,
            "push_events": push_events,
            "report_theme_id": report_theme_id,
            "webhook_enabled": bool(notifications_cfg.get("webhook_enabled", False)),
            "webhook_url": str(notifications_cfg.get("webhook_url", "")),
            "ntfy_enabled": bool(notifications_cfg.get("ntfy_enabled", False)),
            "ntfy_server": str(notifications_cfg.get("ntfy_server", "https://ntfy.sh")),
            "ntfy_topic": str(notifications_cfg.get("ntfy_topic", "")),
        },
        "app": {
            "timezone": app_timezone,
            "timezone_source": app_timezone_source,
        },
        "application_time": _application_time_payload(loaded),
        "ui_theme": ui_theme,
        "thresholds": {
            "download_mbps": _safe_float(thresholds_cfg.get("download_mbps", 0), 0.0),
            "upload_mbps": _safe_float(thresholds_cfg.get("upload_mbps", 0), 0.0),
            "ping_ms": _safe_float(thresholds_cfg.get("ping_ms", 0), 0.0),
            "packet_loss_percent": _safe_float(thresholds_cfg.get("packet_loss_percent", 0), 0.0),
        },
        "contract": {
            "current": {
                "start_date": str(current_contract.get("start_date", "")),
                "end_date": str(current_contract.get("end_date", "")),
                "download_mbps": current_contract.get("download_mbps", 0),
                "upload_mbps": current_contract.get("upload_mbps", 0),
                "reminder_enabled": bool(current_contract.get("reminder_enabled", False)),
                "reminder_days": int(current_contract.get("reminder_days", 31)),
            },
            "history": contract_history,
        },
        "backup": {
            "scheduled_backup_enabled": bool(backup_cfg.get("scheduled_backup_enabled", False)),
            "scheduled_backup_time": str(backup_cfg.get("scheduled_backup_time", "03:00")),
            "scheduled_backup_frequency": str(backup_cfg.get("scheduled_backup_frequency", "daily")),
            "scheduled_backup_include_logs": bool(backup_cfg.get("scheduled_backup_include_logs", True)),
            "max_backups": max(1, _safe_int(backup_cfg.get("max_backups", 10), 10)),
            "backup_password_set": bool(os.getenv("BACKUP_PASSWORD", "").strip()),
        },
    }


def _resolve_recovery_email(config: dict | None = None) -> str:
    """Use the dedicated recovery address first, then fall back to the login email."""
    recovery_email = _normalize_email(os.getenv("RECOVERY_EMAIL", ""))
    if _is_valid_email(recovery_email):
        return recovery_email

    return _resolve_login_email(config)


def _validate_weekly_schedule(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z]+\s+\d{2}:\d{2}$", value.strip()))


def _validate_hhmm(value: str) -> bool:
    if not re.match(r"^\d{2}:\d{2}$", value.strip()):
        return False

    hour_text, minute_text = value.strip().split(":", 1)
    hour = int(hour_text)
    minute = int(minute_text)
    return 0 <= hour <= 23 and 0 <= minute <= 59


def _is_valid_timezone(value: object) -> bool:
    candidate = str(value or "").strip()
    if not candidate:
        return False
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _resolve_app_timezone(config: dict | None = None) -> tuple[str, str]:
    loaded = config or load_config()
    configured = str(loaded.get("app", {}).get("timezone", "") or "").strip()
    if _is_valid_timezone(configured):
        return configured, "settings"

    app_timezone_env = str(os.getenv("APP_TIMEZONE", "") or "").strip()
    if _is_valid_timezone(app_timezone_env):
        return app_timezone_env, "env"

    tz_env = str(os.getenv("TZ", "") or "").strip()
    if _is_valid_timezone(tz_env):
        return tz_env, "env"

    return "UTC", "default"


def _application_time_payload(config: dict | None = None) -> dict[str, str]:
    timezone_name, source = _resolve_app_timezone(config)
    now_local = datetime.now(ZoneInfo(timezone_name))
    offset_raw = now_local.strftime("%z")
    utc_offset = (
        f"{offset_raw[:3]}:{offset_raw[3:]}"
        if len(offset_raw) == 5
        else offset_raw
    )
    return {
        "timezone": timezone_name,
        "timezone_source": source,
        "now_iso": now_local.isoformat(timespec="seconds"),
        "now_display": now_local.strftime("%Y-%m-%d %H:%M:%S"),
        "utc_offset": utc_offset,
    }


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_push_events(payload: object) -> dict[str, bool]:
    normalized = dict(_PUSH_EVENT_DEFAULTS)
    if not isinstance(payload, dict):
        return normalized

    for event_name in _PUSH_EVENT_DEFAULTS:
        if event_name in payload:
            normalized[event_name] = bool(payload.get(event_name))
    return normalized


_WEEKDAY_NAMES = {
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
}


def _clean_theme_id(raw_value: object, fallback: str = "default-dark") -> str:
    theme_id = str(raw_value or "").strip().lower()
    if not theme_id:
        return fallback
    if not re.fullmatch(r"[a-z0-9-]{3,64}", theme_id):
        return fallback
    return theme_id


def _clean_theme_mode(raw_value: object, fallback: str = "system") -> str:
    mode = str(raw_value or "").strip().lower()
    if mode in {"system", "light", "dark"}:
        return mode
    return fallback


def _clean_scan_frequency(raw_value: object, fallback: str = "daily") -> str:
    frequency = str(raw_value or "").strip().lower()
    if frequency in {"daily", "weekly", "monthly", "custom"}:
        return frequency
    return fallback


def _clean_weekday_name(raw_value: object, fallback: str = "Monday") -> str:
    raw = str(raw_value or "").strip().lower()
    if raw not in _WEEKDAY_NAMES:
        return fallback
    return raw.capitalize()


def _ui_theme_preferences(config: dict | None = None) -> dict[str, str]:
    loaded = config or load_config()
    app_cfg = loaded.get("app", {})
    mode = _clean_theme_mode(app_cfg.get("ui_theme_mode", "system"), "system")
    light_theme = _clean_theme_id(app_cfg.get("ui_theme_light", "github-light"), "github-light")
    dark_theme = _clean_theme_id(app_cfg.get("ui_theme_dark", "github-dark"), "github-dark")
    return {
        "mode": mode,
        "light": light_theme,
        "dark": dark_theme,
    }


def _github_project_url(config: dict | None = None) -> str:
    configured = str(os.getenv("GITHUB_REPO_URL", "")).strip()
    if not configured:
        source = config or load_config()
        configured = str(source.get("app", {}).get("github_url", "")).strip()
    if configured.startswith("http://") or configured.startswith("https://"):
        return configured
    return "https://github.com/Sreniok/SpeedPulse"


def _github_sponsors_url(config: dict | None = None) -> str:
    project_url = _github_project_url(config)
    parsed = urlparse(project_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) >= 1 and parsed.netloc:
        return f"https://github.com/sponsors/{path_parts[0]}"
    return "https://github.com/sponsors/Sreniok"



def _normalize_test_times(values: object) -> list[str]:
    if not isinstance(values, list):
        raise HTTPException(status_code=400, detail="Daily scan times must be a list")

    normalized: list[str] = []
    seen: set[str] = set()

    for raw_value in values:
        value = _clean_env_value(raw_value)
        if not _validate_hhmm(value):
            raise HTTPException(status_code=400, detail="Each daily scan time must use HH:MM format")
        if value in seen:
            raise HTTPException(status_code=400, detail="Daily scan times must be unique")
        seen.add(value)
        normalized.append(value)

    if not normalized:
        raise HTTPException(status_code=400, detail="Add at least one daily scan time")

    return sorted(normalized)


def _normalize_scan_custom_days(values: object) -> list[int]:
    if not isinstance(values, list):
        raise HTTPException(status_code=400, detail="Custom scan days must be a list")

    normalized_set: set[int] = set()
    for raw_value in values:
        day = _safe_int(raw_value, 0)
        if day < 1 or day > 31:
            raise HTTPException(status_code=400, detail="Custom scan days must be between 1 and 31")
        normalized_set.add(day)

    normalized = sorted(normalized_set)
    if not normalized:
        raise HTTPException(status_code=400, detail="Select at least one custom scan day")

    return normalized


def _send_settings_test_email(config: dict) -> None:
    mail = load_mail_settings(config)

    msg = MIMEText(
        "This is a test email from SpeedPulse settings.\n\nIf you received this, SMTP setup is working.",
        "plain",
        "utf-8",
    )
    msg["From"] = mail.from_addr
    msg["To"] = mail.to_addr
    msg["Subject"] = "SpeedPulse Test Notification"

    if mail.smtp_port == 465:
        server = smtplib.SMTP_SSL(mail.smtp_server, mail.smtp_port, timeout=30)
    else:
        server = smtplib.SMTP(mail.smtp_server, mail.smtp_port, timeout=30)
        server.starttls()

    try:
        server.login(mail.smtp_username, mail.smtp_password)
        server.send_message(msg)
    finally:
        server.quit()


_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "[::1]"}


def _validate_outbound_url(url: str) -> None:
    """Reject URLs targeting localhost, link-local, or non-HTTP schemes (SSRF prevention)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https scheme")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("URL has no hostname")
    if hostname in _BLOCKED_HOSTS:
        raise ValueError("URL must not target localhost")
    if hostname.startswith("169.254.") or hostname.startswith("fe80:"):
        raise ValueError("URL must not target link-local addresses")
    if hostname.startswith("10.") or hostname.startswith("192.168.") or hostname.startswith("172."):
        LOGGER.info("Outbound URL targets private LAN address: %s", hostname)
    if hostname.endswith(".internal") or hostname.endswith(".local"):
        LOGGER.info("Outbound URL targets internal/mDNS hostname: %s", hostname)


def _send_settings_test_webhook(config: dict) -> None:
    notifications_cfg = config.get("notifications", {})
    if not notifications_cfg.get("webhook_enabled", False):
        raise RuntimeError("Webhook notifications are disabled")

    webhook_url = str(notifications_cfg.get("webhook_url", "")).strip()
    if not webhook_url:
        raise RuntimeError("Webhook URL is empty")

    _validate_outbound_url(webhook_url)

    payload = json.dumps(
        {
            "title": "SpeedPulse test notification",
            "message": "Webhook channel test from dashboard settings.",
            "timestamp": _iso_now(),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        if int(response.status) >= 300:
            raise RuntimeError(f"Webhook returned HTTP {response.status}")


def _send_settings_test_ntfy(config: dict) -> None:
    notifications_cfg = config.get("notifications", {})
    if not notifications_cfg.get("ntfy_enabled", False):
        raise RuntimeError("ntfy notifications are disabled")

    topic = str(notifications_cfg.get("ntfy_topic", "")).strip()
    if not topic:
        raise RuntimeError("ntfy topic is empty")

    base_url = str(notifications_cfg.get("ntfy_server", "https://ntfy.sh")).strip() or "https://ntfy.sh"
    target_url = f"{base_url.rstrip('/')}/{quote(topic, safe='')}"
    _validate_outbound_url(target_url)
    payload = f"SpeedPulse test notification ({_iso_now()})".encode("utf-8")
    request = urllib.request.Request(
        target_url,
        data=payload,
        method="POST",
        headers={
            "Title": "SpeedPulse Test",
            "Priority": "3",
            "Tags": "satellite,white_check_mark",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        if int(response.status) >= 300:
            raise RuntimeError(f"ntfy returned HTTP {response.status}")


def server_setting_payload(config: dict | None = None, force_refresh: bool = False) -> dict:
    return build_server_setting_payload(
        load_config=load_config,
        logger=LOGGER,
        config=config,
        force_refresh=force_refresh,
    )


def _is_setup_mode() -> bool:
    """True when no dashboard credentials have been configured yet."""
    return (
        not os.getenv("DASHBOARD_PASSWORD_HASH", "").strip()
        and not os.getenv("DASHBOARD_PASSWORD", "").strip()
    )


def _ensure_crypto_keys() -> None:
    """Auto-generate APP_SECRET_KEY and AUTH_SALT if missing (setup mode)."""
    global AUTH_SALT

    secret_key = os.getenv("APP_SECRET_KEY", "")
    if not secret_key or len(secret_key) < 32:
        generated = secrets.token_urlsafe(48)
        os.environ["APP_SECRET_KEY"] = generated
        try:
            _update_env_file({"APP_SECRET_KEY": generated})
        except OSError:
            pass

    if not AUTH_SALT:
        generated = secrets.token_hex(16)
        os.environ["AUTH_SALT"] = generated
        AUTH_SALT = generated
        try:
            _update_env_file({"AUTH_SALT": generated})
        except OSError:
            pass


def _maybe_migrate_login_email() -> str:
    current_login_email = _normalize_email(os.getenv("DASHBOARD_LOGIN_EMAIL", ""))
    if _is_valid_email(current_login_email):
        return current_login_email

    candidate = _resolve_login_email()
    if not _is_valid_email(candidate):
        return ""

    env_updates = {"DASHBOARD_LOGIN_EMAIL": candidate}
    if not _is_valid_email(os.getenv("RECOVERY_EMAIL", "")):
        env_updates["RECOVERY_EMAIL"] = candidate

    try:
        _update_env_file(env_updates)
    except OSError as exc:
        LOGGER.warning("Could not persist migrated dashboard login email to .env: %s", exc)

    _apply_runtime_env(env_updates)
    return candidate


def _create_reset_token(login_email: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    store_reset_token(
        token,
        _normalize_email(login_email),
        now + 900,
        now=now,
        max_pending=10,
    )
    return token


def _consume_reset_token(token: str) -> str | None:
    login_email = state_consume_reset_token(token, time.time())
    if not login_email:
        return None
    return _normalize_email(login_email)


def _send_reset_email(to_addr: str, token: str, base_url: str) -> None:
    config = load_config()
    mail = load_mail_settings(config)

    reset_url = f"{base_url.rstrip('/')}/reset-password?token={quote(token)}"
    body = (
        "You requested a password reset for SpeedPulse.\n\n"
        f"Click the link below to reset your password (valid for 15 minutes):\n\n"
        f"{reset_url}\n\n"
        "If you did not request this, you can safely ignore this email."
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = mail.from_addr
    msg["To"] = to_addr
    msg["Subject"] = "SpeedPulse \u2014 Password Reset"

    if mail.smtp_port == 465:
        server = smtplib.SMTP_SSL(mail.smtp_server, mail.smtp_port, timeout=30)
    else:
        server = smtplib.SMTP(mail.smtp_server, mail.smtp_port, timeout=30)
        server.starttls()

    try:
        server.login(mail.smtp_username, mail.smtp_password)
        server.send_message(msg)
    finally:
        server.quit()


def hash_password_pbkdf2(password: str, salt_hex: str, iterations: int) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), iterations)
    return digest.hex()


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify pbkdf2 hash in format: pbkdf2_sha256:iterations:salt_hex:hash_hex."""
    parsed = None
    for separator in (":", "$"):
        parts = stored_hash.split(separator, 3)
        if len(parts) == 4 and parts[0] == "pbkdf2_sha256":
            parsed = parts
            break

    if not parsed:
        return False

    algorithm, iter_text, salt_hex, hash_hex = parsed

    if algorithm != "pbkdf2_sha256":
        return False

    try:
        iterations = int(iter_text)
    except ValueError:
        return False

    computed = hash_password_pbkdf2(password, salt_hex, iterations)
    return hmac.compare_digest(computed, hash_hex)


def _validate_password_hash_format(password_hash: str) -> bool:
    for separator in (":", "$"):
        parts = password_hash.split(separator)
        if len(parts) == 4 and parts[0] == "pbkdf2_sha256":
            return True
    return False


def validate_security_configuration() -> None:
    password_hash = os.getenv("DASHBOARD_PASSWORD_HASH", "").strip()
    password_plain = os.getenv("DASHBOARD_PASSWORD", "").strip()
    login_email_env = _normalize_email(os.getenv("DASHBOARD_LOGIN_EMAIL", ""))
    legacy_username = _clean_env_value(os.getenv("DASHBOARD_USERNAME", ""))

    # ── Setup mode: no credentials at all → allow registration ──
    if not password_hash and not password_plain and not login_email_env and not legacy_username:
        LOGGER.info("No dashboard credentials — starting in setup mode (visit /register)")
        _ensure_crypto_keys()
        return

    # ── Normal mode: full validation ────────────────────────────
    if not AUTH_SALT:
        raise RuntimeError("AUTH_SALT must be set (use a random hex string, e.g. python3 -c 'import secrets; print(secrets.token_hex(16))')")

    secret_key = os.getenv("APP_SECRET_KEY", "")
    if not secret_key or secret_key in {"change-me", "replace-with-long-random-secret"} or len(secret_key) < 32:
        raise RuntimeError("APP_SECRET_KEY must be set to a strong random value (minimum 32 characters)")

    login_email = _maybe_migrate_login_email()
    if not _is_valid_email(login_email):
        raise RuntimeError("DASHBOARD_LOGIN_EMAIL must be set to a valid email address")

    if password_hash:
        if not _validate_password_hash_format(password_hash):
            raise RuntimeError("DASHBOARD_PASSWORD_HASH format is invalid")
    elif password_plain:
        LOGGER.warning(
            "Plain DASHBOARD_PASSWORD is deprecated and will be removed in a future release. "
            "Use 'python3 generate_password_hash.py' to create DASHBOARD_PASSWORD_HASH instead."
        )
        if password_plain in {"change-me", "admin", "password"}:
            raise RuntimeError("DASHBOARD_PASSWORD must not use default/insecure value")
        new_hash = build_password_hash(password_plain)
        env_updates = {"DASHBOARD_PASSWORD_HASH": new_hash, "DASHBOARD_PASSWORD": ""}
        try:
            _update_env_file(env_updates)
        except OSError:
            LOGGER.warning("Could not persist DASHBOARD_PASSWORD_HASH to .env (read-only filesystem?)")
        _apply_runtime_env(env_updates)
    else:
        raise RuntimeError(
            "DASHBOARD_PASSWORD_HASH is required. "
            "Generate one with: python3 generate_password_hash.py"
        )


def get_serializer() -> URLSafeSerializer:
    return URLSafeSerializer(os.getenv("APP_SECRET_KEY", ""), salt=AUTH_SALT)


def _extract_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _is_login_blocked(client_ip: str) -> int:
    return state_blocked_seconds(client_ip, time.time())


def _register_failed_login(client_ip: str) -> int:
    max_attempts = _env_int("LOGIN_MAX_ATTEMPTS", 5)
    window_seconds = _env_int("LOGIN_WINDOW_SECONDS", 900)
    block_seconds = _env_int("LOGIN_BLOCK_SECONDS", 900)
    return state_register_failed_login(
        client_ip,
        time.time(),
        max_attempts=max_attempts,
        window_seconds=window_seconds,
        block_seconds=block_seconds,
    )


def _clear_failed_logins(client_ip: str) -> None:
    state_clear_login_failures(client_ip)


def verify_login_credentials(login_email: str, password: str) -> bool:
    expected_login_email = _resolve_login_email()
    password_hash = os.getenv("DASHBOARD_PASSWORD_HASH", "").strip()
    password_plain = os.getenv("DASHBOARD_PASSWORD", "").strip()

    provided_login_email = _normalize_email(login_email)
    if not expected_login_email or not hmac.compare_digest(provided_login_email, expected_login_email):
        return False

    if password_hash:
        return verify_password(password, password_hash)

    return hmac.compare_digest(password, password_plain)


def current_session(request: Request) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None

    try:
        payload = get_serializer().loads(token)
    except BadSignature:
        return None

    login_email = _normalize_email(
        payload.get("login_email") or payload.get("username") or "",
    )
    exp = payload.get("exp")
    csrf = payload.get("csrf")

    if not login_email:
        login_email = _resolve_login_email()

    if not login_email or not exp or not csrf:
        return None

    if int(exp) < int(time.time()):
        return None

    with SESSION_VERSION_LOCK:
        required_version = SESSION_VERSION
    token_version = payload.get("sv", 1)
    if token_version < required_version:
        return None

    payload["login_email"] = login_email
    payload["username"] = login_email
    return payload


def require_session(request: Request) -> dict:
    session = current_session(request)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return session


def require_csrf(request: Request, session: dict) -> None:
    sent_token = request.headers.get("X-CSRF-Token", "")
    expected_token = str(session.get("csrf", ""))
    if not sent_token or not hmac.compare_digest(sent_token, expected_token):
        LOGGER.warning("CSRF validation failed for %s %s", request.method, request.url.path)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


APP.include_router(
    build_system_router(
        logo_path=LOGO_PATH,
        version=__version__,
        require_session=require_session,
        build_readiness_state=_build_system_readiness_state,
    )
)
APP.include_router(
    build_auth_router(
        templates=TEMPLATES,
        flash_cookie=FLASH_COOKIE,
        session_cookie=SESSION_COOKIE,
        get_serializer=get_serializer,
        is_setup_mode=_is_setup_mode,
        resolve_recovery_email=_resolve_recovery_email,
        normalize_email=_normalize_email,
        extract_client_ip=_extract_client_ip,
        is_login_blocked=_is_login_blocked,
        verify_login_credentials=verify_login_credentials,
        register_failed_login=_register_failed_login,
        clear_failed_logins=_clear_failed_logins,
        env_int=_env_int,
        get_session_version=_current_session_version,
        is_secure_request=_is_secure_request,
        is_valid_email=_is_valid_email,
        build_password_hash=build_password_hash,
        update_env_file=_update_env_file,
        apply_runtime_env=_apply_runtime_env,
        logger=LOGGER,
        create_reset_token=lambda login_email: _create_reset_token(login_email),
        send_reset_email=lambda to_addr, token, base_url: _send_reset_email(to_addr, token, base_url),
        resolve_login_email=_resolve_login_email,
        consume_reset_token=_consume_reset_token,
        rotate_session_version=_rotate_session_version,
    )
)
APP.include_router(
    build_backup_router(
        require_session=require_session,
        require_csrf=require_csrf,
        load_config=load_config,
        logger=LOGGER,
        create_backup_fn=lambda password, include_logs: create_backup(password, include_logs=include_logs),
        delete_backup_fn=lambda filename, config: delete_backup(filename, config),
        get_backup_path_fn=lambda filename, config: get_backup_path(filename, config),
        list_backups_fn=lambda config: list_backups(config),
        restore_backup_fn=lambda data, password: restore_backup(data, password),
        save_backup_to_path_fn=lambda encrypted, filename, config: save_backup_to_path(encrypted, filename, config),
        validate_backup_fn=lambda data, password: validate_backup(data, password),
    )
)
APP.include_router(
    build_dashboard_router(
        templates=TEMPLATES,
        current_session=current_session,
        load_config=load_config,
        detected_account_network_identity=_detected_account_network_identity,
        github_project_url=_github_project_url,
        github_sponsors_url=_github_sponsors_url,
        ui_theme_preferences=_ui_theme_preferences,
        require_session=require_session,
        require_csrf=require_csrf,
        build_dashboard_payload_fn=lambda days, mode: build_dashboard_payload(days, mode=mode),
        load_measurement_entries_fn=load_measurement_entries,
        filter_entries_for_mode_fn=lambda entries, now, days, mode: _filter_entries_for_mode(entries, now, days, mode),
        clean_theme_id_fn=_clean_theme_id,
        resolve_report_theme_id_fn=resolve_report_theme_id,
        build_report_html_fn=build_report_html,
        run_weekly_report_now_fn=lambda: run_weekly_report_now(),
    )
)
APP.include_router(
    build_manual_runs_router(
        require_session=require_session,
        require_csrf=require_csrf,
        env_int=_env_int,
        get_last_manual_speedtest_at=_get_last_manual_speedtest_at,
        set_last_manual_speedtest_at=_set_last_manual_speedtest_at,
        manual_run_snapshot=_manual_run_snapshot,
        load_speedtest_completion_state=load_speedtest_completion_state,
        iso_from_epoch=_iso_from_epoch,
        load_config=load_config,
        resolve_server_label=lambda server_id, config=None: _resolve_server_label(server_id, config=config),
        try_acquire_manual_speedtest_lock=_try_acquire_manual_speedtest_lock,
        release_manual_speedtest_lock=_release_manual_speedtest_lock,
        start_manual_run_state=_start_manual_run_state,
        start_manual_speedtest_thread=_start_manual_speedtest_thread,
        update_manual_run_state=_update_manual_run_state,
        iso_now=_iso_now,
        logger=LOGGER,
    )
)


def _filter_entries_for_mode(entries: list[dict], now: datetime, days: int, mode: str) -> list[dict]:
    if mode == "today":
        return [entry for entry in entries if entry["timestamp"].date() == now.date()]

    cutoff = now - timedelta(days=days)
    return [entry for entry in entries if entry["timestamp"] >= cutoff]


def _entry_is_healthy(entry: dict, thresholds: dict) -> bool:
    return (
        entry["download_mbps"] >= thresholds.get("download_mbps", 0)
        and entry["upload_mbps"] >= thresholds.get("upload_mbps", 0)
        and entry["ping_ms"] <= thresholds.get("ping_ms", 999999)
        and entry["packet_loss_percent"] <= thresholds.get("packet_loss_percent", 999999)
    )


def _entry_breach_types(entry: dict, thresholds: dict) -> list[str]:
    breach_types: list[str] = []
    if entry["download_mbps"] < thresholds.get("download_mbps", 0):
        breach_types.append("download")
    if entry["upload_mbps"] < thresholds.get("upload_mbps", 0):
        breach_types.append("upload")
    if entry["ping_ms"] > thresholds.get("ping_ms", 999999):
        breach_types.append("ping")
    if entry["packet_loss_percent"] > thresholds.get("packet_loss_percent", 999999):
        breach_types.append("packet_loss")
    return breach_types


def _incident_severity(breach_counts: Counter[str]) -> str:
    if not breach_counts:
        return "low"
    if len(breach_counts) >= 3:
        return "high"
    if "download" in breach_counts or "upload" in breach_counts:
        return "high" if len(breach_counts) >= 2 else "medium"
    return "medium" if len(breach_counts) >= 2 else "low"


def _sla_grade(compliance_pct: float, total_tests: int) -> str:
    if total_tests == 0:
        return "N/A"
    if compliance_pct >= 99:
        return "A"
    if compliance_pct >= 97:
        return "B"
    if compliance_pct >= 94:
        return "C"
    if compliance_pct >= 90:
        return "D"
    return "F"


def _finalize_incident(raw_incident: dict, ongoing: bool) -> dict:
    breach_counts: Counter[str] = raw_incident["breach_counts"]
    breach_types = [name for name, _ in breach_counts.most_common()]
    primary_server = "Unknown"
    if raw_incident["servers"]:
        primary_server = raw_incident["servers"].most_common(1)[0][0]

    end_marker = raw_incident["end_at"]
    if not ongoing and raw_incident.get("resolved_at") is not None:
        end_marker = raw_incident["resolved_at"]

    duration_minutes = max(0.0, round((end_marker - raw_incident["start_at"]).total_seconds() / 60, 1))
    headline = " / ".join(
        {
            "download": "Download below floor",
            "upload": "Upload below floor",
            "ping": "Ping above ceiling",
            "packet_loss": "Packet loss above ceiling",
        }[breach_type]
        for breach_type in breach_types[:3]
    )

    return {
        "started_at": raw_incident["start_at"].isoformat(),
        "ended_at": raw_incident["end_at"].isoformat(),
        "resolved_at": raw_incident.get("resolved_at").isoformat() if raw_incident.get("resolved_at") else None,
        "ongoing": ongoing,
        "tests_affected": raw_incident["tests_affected"],
        "duration_minutes": duration_minutes,
        "breach_types": breach_types,
        "breach_counts": dict(breach_counts),
        "primary_server": primary_server,
        "severity": _incident_severity(breach_counts),
        "headline": headline,
        "summary": (
            f"{raw_incident['tests_affected']} affected test"
            f"{'' if raw_incident['tests_affected'] == 1 else 's'}"
            f"{' and still ongoing' if ongoing else ''}"
        ),
    }


def _build_incident_history(entries: list[dict], thresholds: dict) -> list[dict]:
    incidents: list[dict] = []
    current: dict | None = None

    for entry in entries:
        breach_types = _entry_breach_types(entry, thresholds)
        if breach_types:
            if current is None:
                current = {
                    "start_at": entry["timestamp"],
                    "end_at": entry["timestamp"],
                    "resolved_at": None,
                    "tests_affected": 0,
                    "breach_counts": Counter(),
                    "servers": Counter(),
                }
            current["end_at"] = entry["timestamp"]
            current["tests_affected"] += 1
            current["breach_counts"].update(breach_types)
            current["servers"].update([entry.get("server", "Unknown")])
            continue

        if current is not None:
            current["resolved_at"] = entry["timestamp"]
            incidents.append(_finalize_incident(current, ongoing=False))
            current = None

    if current is not None:
        incidents.append(_finalize_incident(current, ongoing=True))

    incidents.sort(key=lambda incident: incident["started_at"], reverse=True)
    return incidents[:8]


def _build_sla_summary(
    recent_entries: list[dict],
    thresholds: dict,
    incidents: list[dict],
    scheduled_tests_per_day: int,
    days: int,
    mode: str,
) -> dict:
    total_tests = len(recent_entries)
    healthy_tests = sum(1 for entry in recent_entries if _entry_is_healthy(entry, thresholds))
    breach_tests = total_tests - healthy_tests
    compliance_pct = round((healthy_tests / total_tests) * 100, 1) if total_tests else 0.0

    expected_tests = 0
    if scheduled_tests_per_day > 0:
        expected_tests = scheduled_tests_per_day if mode == "today" else scheduled_tests_per_day * max(days, 1)
    coverage_pct = round(min(total_tests / expected_tests, 1) * 100, 1) if expected_tests else 100.0

    return {
        "grade": _sla_grade(compliance_pct, total_tests),
        "compliance_pct": compliance_pct,
        "healthy_tests": healthy_tests,
        "breach_tests": breach_tests,
        "incident_count": len(incidents),
        "sample_coverage_pct": coverage_pct,
        "expected_tests": expected_tests,
        "window_label": "Today" if mode == "today" else f"Last {days} days",
    }


def build_dashboard_payload(days: int = 30, mode: str = "days") -> dict:
    config = load_config()
    server_settings = server_setting_payload(config)
    thresholds = config.get("thresholds", {})
    scheduling = config.get("scheduling", {})
    entries = load_measurement_entries(config)
    detected_identity = _detected_account_network_identity(config, entries=entries)
    application_time = _application_time_payload(config)
    now = datetime.now(ZoneInfo(application_time["timezone"])).replace(tzinfo=None)
    scan_enabled = bool(scheduling.get("scan_enabled", True))
    scan_frequency = _clean_scan_frequency(scheduling.get("scan_frequency", "daily"), "daily")
    scheduled_tests_per_day = len(scheduling.get("test_times", [])) if scan_enabled else 0
    today_entries = [entry for entry in entries if entry["timestamp"].date() == now.date()]
    today_manual_entries = [entry for entry in today_entries if str(entry.get("source", "")).lower() == "manual"]
    today_scheduled_entries = [entry for entry in today_entries if str(entry.get("source", "")).lower() != "manual"]

    recent_entries = _filter_entries_for_mode(entries, now, days, mode)

    # Previous period for comparison
    if mode == "today":
        yesterday = now - timedelta(days=1)
        prev_entries = [entry for entry in entries if entry["timestamp"].date() == yesterday.date()]
    else:
        prev_cutoff_start = now - timedelta(days=days * 2)
        prev_cutoff_end = now - timedelta(days=days)
        prev_entries = [entry for entry in entries if prev_cutoff_start <= entry["timestamp"] < prev_cutoff_end]

    def avg(values: list[float]) -> float:
        return round(sum(values) / len(values), 2) if values else 0.0

    download_values = [entry["download_mbps"] for entry in recent_entries]
    upload_values = [entry["upload_mbps"] for entry in recent_entries]
    ping_values = [entry["ping_ms"] for entry in recent_entries]
    jitter_values = [entry["jitter_ms"] for entry in recent_entries]
    packet_loss_values = [entry["packet_loss_percent"] for entry in recent_entries]

    violations = {
        "download": sum(1 for value in download_values if value < thresholds.get("download_mbps", 0)),
        "upload": sum(1 for value in upload_values if value < thresholds.get("upload_mbps", 0)),
        "ping": sum(1 for value in ping_values if value > thresholds.get("ping_ms", 999999)),
        "packet_loss": sum(
            1 for value in packet_loss_values if value > thresholds.get("packet_loss_percent", 999999)
        ),
    }
    incidents = _build_incident_history(recent_entries, thresholds)
    sla = _build_sla_summary(
        recent_entries,
        thresholds,
        incidents,
        scheduled_tests_per_day,
        days,
        mode,
    )

    timeseries = [
        {
            "timestamp": entry["timestamp"].isoformat(),
            "download_mbps": entry["download_mbps"],
            "upload_mbps": entry["upload_mbps"],
            "ping_ms": entry["ping_ms"],
            "jitter_ms": entry["jitter_ms"],
            "packet_loss_percent": entry["packet_loss_percent"],
        }
        for entry in recent_entries
    ]

    latest_entries = [
        {
            "timestamp": entry["timestamp"].strftime("%Y-%m-%d %H:%M"),
            "timestamp_iso": entry["timestamp"].isoformat(),
            "download_mbps": entry["download_mbps"],
            "upload_mbps": entry["upload_mbps"],
            "ping_ms": entry["ping_ms"],
            "jitter_ms": entry["jitter_ms"],
            "packet_loss_percent": entry["packet_loss_percent"],
            "server": entry.get("server", "Unknown"),
            "source": entry.get("source", "unknown"),
            "status": "Completed",
            "healthy": _entry_is_healthy(entry, thresholds),
        }
        for entry in recent_entries
    ][::-1]

    last_test_at = recent_entries[-1]["timestamp"].isoformat() if recent_entries else None

    return {
        "mode": mode,
        "window_days": days,
        "total_tests": len(recent_entries),
        "scheduled_tests_per_day": scheduled_tests_per_day,
        "today_tests": len(today_entries),
        "today_scheduled_tests": len(today_scheduled_entries),
        "today_manual_tests": len(today_manual_entries),
        "detected_provider": detected_identity["provider"],
        "detected_ip_address": detected_identity["ip_address"],
        "averages": {
            "download_mbps": avg(download_values),
            "upload_mbps": avg(upload_values),
            "ping_ms": avg(ping_values),
            "jitter_ms": avg(jitter_values),
            "packet_loss_percent": avg(packet_loss_values),
        },
        "previous_averages": {
            "download_mbps": avg([e["download_mbps"] for e in prev_entries]),
            "upload_mbps": avg([e["upload_mbps"] for e in prev_entries]),
            "ping_ms": avg([e["ping_ms"] for e in prev_entries]),
            "total_tests": len(prev_entries),
        },
        "max": {
            "download_mbps": round(max(download_values), 2) if download_values else 0.0,
            "upload_mbps": round(max(upload_values), 2) if upload_values else 0.0,
            "ping_ms": round(max(ping_values), 2) if ping_values else 0.0,
        },
        "min": {
            "download_mbps": round(min(download_values), 2) if download_values else 0.0,
            "upload_mbps": round(min(upload_values), 2) if upload_values else 0.0,
            "ping_ms": round(min(ping_values), 2) if ping_values else 0.0,
        },
        "violations": violations,
        "thresholds": thresholds,
        "scheduling": {
            "test_times": scheduling.get("test_times", []),
            "weekly_report_time": scheduling.get("weekly_report_time", ""),
            "monthly_report_time": scheduling.get("monthly_report_time", ""),
            "scan_enabled": scan_enabled,
            "scan_frequency": scan_frequency,
            "scan_weekly_day": _clean_weekday_name(scheduling.get("scan_weekly_day", "Monday"), "Monday"),
            "scan_monthly_day": max(1, min(31, _safe_int(scheduling.get("scan_monthly_day", 1), 1))),
            "scan_custom_days": sorted(
                {
                    day
                    for day in (_safe_int(value, 0) for value in scheduling.get("scan_custom_days", []))
                    if 1 <= day <= 31
                }
            ),
        },
        "timeseries": timeseries,
        "latest_tests": latest_entries,
        "incidents": incidents,
        "last_test_at": last_test_at,
        "range_label": "Today" if mode == "today" else f"Last {days} days",
        "sla": sla,
        "server_selection_id": server_settings["selected_id"],
        "server_selection_label": server_settings["selected_label"],
        "application_time": application_time,
    }


@APP.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    return response




@APP.get("/api/settings/server")
def get_server_settings(request: Request) -> JSONResponse:
    require_session(request)
    return JSONResponse(server_setting_payload(force_refresh=True))


@APP.post("/api/settings/server")
async def update_server_settings(request: Request) -> JSONResponse:
    session = require_session(request)
    require_csrf(request, session)

    payload = await request.json()
    selected_id = str(payload.get("server_id", "") or "").strip()
    if selected_id and not selected_id.isdigit():
        raise HTTPException(status_code=400, detail="server_id must be numeric or empty")

    config = load_config()
    config.setdefault("speedtest", {})["server_id"] = selected_id
    save_config(config)

    settings_payload = server_setting_payload(config, force_refresh=True)
    settings_payload["message"] = (
        "Server selection updated to auto mode."
        if not selected_id
        else f"Server selection updated to {settings_payload['selected_label']}."
    )
    return JSONResponse(settings_payload)


@APP.get("/api/settings/notifications")
def get_notification_settings(request: Request) -> JSONResponse:
    require_session(request)
    return JSONResponse(dashboard_settings_payload())


@APP.post("/api/settings/appearance")
async def update_appearance_settings(request: Request) -> JSONResponse:
    session = require_session(request)
    require_csrf(request, session)

    payload = await request.json()
    ui_theme_payload = payload.get("ui_theme", {})
    ui_theme_mode = _clean_theme_mode(
        ui_theme_payload.get("mode", payload.get("ui_theme_mode", "system")),
        "system",
    )
    ui_theme_light = _clean_theme_id(
        ui_theme_payload.get("light", payload.get("ui_theme_light", "github-light")),
        "github-light",
    )
    ui_theme_dark = _clean_theme_id(
        ui_theme_payload.get("dark", payload.get("ui_theme_dark", "github-dark")),
        "github-dark",
    )
    report_theme_id = _clean_theme_id(payload.get("report_theme_id", "default-dark"))

    config = load_config()
    app_cfg = config.setdefault("app", {})
    notifications_cfg = config.setdefault("notifications", {})
    app_cfg["ui_theme_mode"] = ui_theme_mode
    app_cfg["ui_theme_light"] = ui_theme_light
    app_cfg["ui_theme_dark"] = ui_theme_dark
    notifications_cfg["report_theme_id"] = report_theme_id
    save_config(config)

    response_payload = dashboard_settings_payload(config)
    response_payload["message"] = "Appearance saved."
    response_payload["restart_required"] = False
    return JSONResponse(response_payload)


@APP.post("/api/settings/notifications")
async def update_notification_settings(request: Request) -> JSONResponse:
    session = require_session(request)
    require_csrf(request, session)

    payload = await request.json()

    account_name = _clean_env_value(payload.get("account_name", ""))
    broadband_provider = _clean_env_value(payload.get("broadband_provider", ""))
    broadband_account_number = _clean_env_value(payload.get("broadband_account_number", ""))
    smtp_server = _clean_env_value(payload.get("smtp_server", ""))
    smtp_username = _clean_env_value(payload.get("smtp_username", ""))
    smtp_password = str(payload.get("smtp_password", "") or "")
    email_from = _clean_env_value(payload.get("email_from", ""))
    weekly_report_time = _clean_env_value(payload.get("weekly_report_time", "Monday 08:00"))
    monthly_report_time = _clean_env_value(payload.get("monthly_report_time", "08:00"))
    app_timezone = _clean_env_value(payload.get("app_timezone", ""))
    scan_enabled = bool(payload.get("scan_enabled", True))
    scan_frequency = _clean_scan_frequency(payload.get("scan_frequency", "daily"), "daily")
    scan_weekly_day = _clean_weekday_name(payload.get("scan_weekly_day", "Monday"), "Monday")
    scan_monthly_day = _safe_int(payload.get("scan_monthly_day", 1), 1)
    scan_custom_days = _normalize_scan_custom_days(payload.get("scan_custom_days", [scan_monthly_day]))
    test_times = _normalize_test_times(payload.get("test_times", ["08:00", "16:00", "22:00"]))
    selected_server_id = _clean_env_value(payload.get("server_id", ""))
    push_events = _normalize_push_events(payload.get("push_events", {}))
    report_theme_id = _clean_theme_id(payload.get("report_theme_id", "default-dark"))
    ui_theme_payload = payload.get("ui_theme", {})
    ui_theme_mode = _clean_theme_mode(
        ui_theme_payload.get("mode", payload.get("ui_theme_mode", "system")),
        "system",
    )
    ui_theme_light = _clean_theme_id(
        ui_theme_payload.get("light", payload.get("ui_theme_light", "github-light")),
        "github-light",
    )
    ui_theme_dark = _clean_theme_id(
        ui_theme_payload.get("dark", payload.get("ui_theme_dark", "github-dark")),
        "github-dark",
    )

    try:
        smtp_port = int(payload.get("smtp_port", 465))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="SMTP port must be numeric") from None

    if selected_server_id and not selected_server_id.isdigit():
        raise HTTPException(status_code=400, detail="server_id must be numeric or empty")

    if smtp_port < 1 or smtp_port > 65535:
        raise HTTPException(status_code=400, detail="SMTP port must be in range 1-65535")

    if not smtp_server or not smtp_username or not email_from:
        raise HTTPException(status_code=400, detail="SMTP server, username, and from address are required")

    if not _validate_weekly_schedule(weekly_report_time):
        raise HTTPException(status_code=400, detail="Weekly report time must use format like 'Monday 08:00'")
    if not _validate_hhmm(monthly_report_time):
        raise HTTPException(status_code=400, detail="Monthly report time must use HH:MM format")
    if scan_monthly_day < 1 or scan_monthly_day > 31:
        raise HTTPException(status_code=400, detail="Monthly scan day must be between 1 and 31")
    if scan_frequency == "custom" and not scan_custom_days:
        raise HTTPException(status_code=400, detail="Select at least one custom scan day")

    config = load_config()
    if not app_timezone:
        app_timezone, _ = _resolve_app_timezone(config)
    if not _is_valid_timezone(app_timezone):
        raise HTTPException(
            status_code=400,
            detail="Application timezone must be a valid IANA timezone (for example: Europe/Warsaw).",
        )
    account_cfg = config.setdefault("account", {})
    email_cfg = config.setdefault("email", {})
    scheduling_cfg = config.setdefault("scheduling", {})
    notifications_cfg = config.setdefault("notifications", {})
    speedtest_cfg = config.setdefault("speedtest", {})
    app_cfg = config.setdefault("app", {})

    detected_identity = _detected_account_network_identity(config)
    effective_provider = detected_identity["provider"] or broadband_provider

    account_cfg["name"] = account_name
    account_cfg["provider"] = effective_provider
    account_cfg["ip_address"] = detected_identity["ip_address"]
    account_cfg["number"] = broadband_account_number

    email_cfg["smtp_server"] = smtp_server
    email_cfg["smtp_port"] = smtp_port
    email_cfg["from"] = email_from
    email_cfg["send_realtime_alerts"] = bool(payload.get("send_realtime_alerts", True))

    scheduling_cfg["test_times"] = test_times
    scheduling_cfg["weekly_report_time"] = weekly_report_time
    scheduling_cfg["monthly_report_time"] = monthly_report_time
    scheduling_cfg["scan_enabled"] = scan_enabled
    scheduling_cfg["scan_frequency"] = scan_frequency
    scheduling_cfg["scan_weekly_day"] = scan_weekly_day
    scheduling_cfg["scan_monthly_day"] = scan_monthly_day
    scheduling_cfg["scan_custom_days"] = scan_custom_days
    speedtest_cfg["server_id"] = selected_server_id

    notifications_cfg["weekly_report_enabled"] = bool(payload.get("weekly_report_enabled", True))
    notifications_cfg["monthly_report_enabled"] = bool(payload.get("monthly_report_enabled", False))
    notifications_cfg["push_events"] = push_events
    notifications_cfg["report_theme_id"] = report_theme_id
    notifications_cfg["webhook_enabled"] = bool(payload.get("webhook_enabled", False))
    notifications_cfg["webhook_url"] = _clean_env_value(payload.get("webhook_url", ""))
    notifications_cfg["ntfy_enabled"] = bool(payload.get("ntfy_enabled", False))
    notifications_cfg["ntfy_server"] = _clean_env_value(payload.get("ntfy_server", "https://ntfy.sh")) or "https://ntfy.sh"
    notifications_cfg["ntfy_topic"] = _clean_env_value(payload.get("ntfy_topic", ""))
    app_cfg["ui_theme_mode"] = ui_theme_mode
    app_cfg["ui_theme_light"] = ui_theme_light
    app_cfg["ui_theme_dark"] = ui_theme_dark
    app_cfg["timezone"] = app_timezone

    contract_payload = payload.get("contract", {})
    if contract_payload:
        contract_current = contract_payload.get("current", {})
        contract_cfg = config.setdefault("contract", {})
        current = contract_cfg.setdefault("current", {})
        current["start_date"] = _clean_env_value(contract_current.get("start_date", ""))
        current["end_date"] = _clean_env_value(contract_current.get("end_date", ""))
        try:
            current["download_mbps"] = float(contract_current.get("download_mbps", 0) or 0)
        except (TypeError, ValueError):
            current["download_mbps"] = 0
        try:
            current["upload_mbps"] = float(contract_current.get("upload_mbps", 0) or 0)
        except (TypeError, ValueError):
            current["upload_mbps"] = 0
        current["reminder_enabled"] = bool(contract_current.get("reminder_enabled", False))
        try:
            current["reminder_days"] = max(1, int(contract_current.get("reminder_days", 31) or 31))
        except (TypeError, ValueError):
            current["reminder_days"] = 31

    thresholds_payload = payload.get("thresholds", {})
    if thresholds_payload:
        thresholds_cfg = config.setdefault("thresholds", {})
        try:
            download_floor = float(thresholds_payload.get("download_mbps", thresholds_cfg.get("download_mbps", 0)) or 0)
            upload_floor = float(thresholds_payload.get("upload_mbps", thresholds_cfg.get("upload_mbps", 0)) or 0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Download and upload thresholds must be numeric") from None

        if download_floor < 0 or upload_floor < 0:
            raise HTTPException(status_code=400, detail="Download and upload thresholds must be 0 or higher")

        thresholds_cfg["download_mbps"] = download_floor
        thresholds_cfg["upload_mbps"] = upload_floor

    backup_payload = payload.get("backup", {})
    if backup_payload:
        backup_cfg = config.setdefault("backup", {})
        backup_cfg["scheduled_backup_enabled"] = bool(backup_payload.get("scheduled_backup_enabled", False))
        raw_time = _clean_env_value(backup_payload.get("scheduled_backup_time", "03:00"))
        if not re.fullmatch(r"\d{2}:\d{2}", raw_time):
            raise HTTPException(status_code=400, detail="Backup time must use HH:MM format")
        backup_cfg["scheduled_backup_time"] = raw_time
        freq = _clean_env_value(backup_payload.get("scheduled_backup_frequency", "daily"))
        if freq not in {"daily", "weekly", "monthly"}:
            raise HTTPException(status_code=400, detail="Backup frequency must be daily, weekly, or monthly")
        backup_cfg["scheduled_backup_frequency"] = freq
        backup_cfg["scheduled_backup_include_logs"] = bool(backup_payload.get("scheduled_backup_include_logs", True))
        try:
            max_backups = int(backup_payload.get("max_backups", backup_cfg.get("max_backups", 10)) or 10)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Backup retention must be numeric") from None
        if max_backups < 1 or max_backups > 365:
            raise HTTPException(status_code=400, detail="Backup retention must be between 1 and 365")
        backup_cfg["max_backups"] = max_backups

    backup_password = str(payload.get("backup_password", "") or "")
    if backup_password.strip() and len(backup_password.strip()) < 6:
        raise HTTPException(
            status_code=400,
            detail="Backup password must be at least 6 characters.",
        )

    save_config(config)

    env_updates = {
        "SMTP_SERVER": smtp_server,
        "SMTP_PORT": str(smtp_port),
        "SMTP_USERNAME": smtp_username,
        "EMAIL_FROM": email_from,
        "APP_TIMEZONE": app_timezone,
        "TZ": app_timezone,
    }
    if smtp_password.strip():
        if encrypted_secret_store_enabled():
            try:
                set_app_secret("smtp_password", smtp_password)
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to store SMTP password securely: {exc}",
                ) from exc
            env_updates["SMTP_PASSWORD"] = ""
        else:
            env_updates["SMTP_PASSWORD"] = smtp_password
    if backup_password.strip():
        env_updates["BACKUP_PASSWORD"] = backup_password

    try:
        _update_env_file(env_updates)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update .env: {exc}") from exc

    _apply_runtime_env(env_updates)

    response_payload = dashboard_settings_payload(config)
    response_payload["message"] = (
        "Settings saved. Schedule changes will be picked up automatically within 10 seconds."
    )
    response_payload["restart_required"] = False
    return JSONResponse(response_payload)


@APP.post("/api/settings/notifications/test")
async def test_notification_channels(request: Request) -> JSONResponse:
    session = require_session(request)
    require_csrf(request, session)

    payload = await request.json()
    channel = str(payload.get("channel", "all") or "all").strip().lower()
    if channel not in {"all", "email", "webhook", "ntfy"}:
        raise HTTPException(status_code=400, detail="channel must be one of: all, email, webhook, ntfy")

    config = load_config()
    results: dict[str, str] = {}

    if channel in {"all", "email"}:
        try:
            _send_settings_test_email(config)
            results["email"] = "sent"
        except Exception as exc:
            results["email"] = f"failed: {exc}"

    if channel in {"all", "webhook"}:
        try:
            _send_settings_test_webhook(config)
            results["webhook"] = "sent"
        except Exception as exc:
            results["webhook"] = f"failed: {exc}"

    if channel in {"all", "ntfy"}:
        try:
            _send_settings_test_ntfy(config)
            results["ntfy"] = "sent"
        except Exception as exc:
            results["ntfy"] = f"failed: {exc}"

    success_count = sum(1 for value in results.values() if value == "sent")
    if success_count == 0:
        return JSONResponse({"message": "No test notifications were sent.", "results": results}, status_code=400)

    return JSONResponse({"message": f"Sent {success_count} notification test(s).", "results": results})


@APP.post("/api/settings/password")
async def update_dashboard_password(request: Request) -> JSONResponse:
    session = require_session(request)
    require_csrf(request, session)

    payload = await request.json()
    current_password = str(payload.get("current_password", "") or "")
    new_password = str(payload.get("new_password", "") or "")
    confirm_password = str(payload.get("confirm_password", "") or "")

    if not current_password or not new_password or not confirm_password:
        raise HTTPException(status_code=400, detail="Current password, new password, and confirmation are required")

    if not verify_login_credentials(str(session.get("login_email", "")), current_password):
        raise HTTPException(status_code=403, detail="Current password is incorrect")

    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="New password and confirmation do not match")

    if len(new_password) < 10:
        raise HTTPException(status_code=400, detail="New password must be at least 10 characters")

    new_hash = build_password_hash(new_password)
    env_updates = {
        "DASHBOARD_PASSWORD_HASH": new_hash,
        "DASHBOARD_PASSWORD": "",
    }

    try:
        _update_env_file(env_updates)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update .env: {exc}") from exc

    _apply_runtime_env(env_updates)

    global SESSION_VERSION
    with SESSION_VERSION_LOCK:
        SESSION_VERSION = bump_session_version()
    LOGGER.info("Session version bumped — all existing sessions invalidated after password change")

    return JSONResponse({"message": "Dashboard password updated. You will be redirected to login."})


@APP.post("/api/settings/user-account")
async def update_user_account_settings(request: Request) -> JSONResponse:
    session = require_session(request)
    require_csrf(request, session)

    payload = await request.json()
    login_email = _normalize_email(payload.get("login_email", ""))
    notification_email = _normalize_email(payload.get("notification_email", ""))
    current_password = str(payload.get("current_password", "") or "")
    new_password = str(payload.get("new_password", "") or "")
    confirm_password = str(payload.get("confirm_password", "") or "")

    if not _is_valid_email(login_email):
        raise HTTPException(status_code=400, detail="Enter a valid login email address")
    if not _is_valid_email(notification_email):
        raise HTTPException(status_code=400, detail="Enter a valid notification email address")

    current_login_email = _resolve_login_email()
    config = load_config()
    current_notification_email = _normalize_email(config.get("email", {}).get("to", ""))

    env_updates: dict[str, str] = {}
    changed_items: list[str] = []

    if login_email != current_login_email:
        env_updates.update(
            {
                "DASHBOARD_LOGIN_EMAIL": login_email,
                "DASHBOARD_USERNAME": "",
                "RECOVERY_EMAIL": login_email,
            }
        )
        changed_items.append("login email")

    password_change_requested = bool(current_password or new_password or confirm_password)
    if password_change_requested:
        if not current_password or not new_password or not confirm_password:
            raise HTTPException(
                status_code=400,
                detail="Current password, new password, and confirmation are required to change password",
            )
        if not verify_login_credentials(str(session.get("login_email", "")), current_password):
            raise HTTPException(status_code=403, detail="Current password is incorrect")
        if new_password != confirm_password:
            raise HTTPException(status_code=400, detail="New password and confirmation do not match")
        if len(new_password) < 10:
            raise HTTPException(status_code=400, detail="New password must be at least 10 characters")

        env_updates.update(
            {
                "DASHBOARD_PASSWORD_HASH": build_password_hash(new_password),
                "DASHBOARD_PASSWORD": "",
            }
        )
        changed_items.append("password")

    notification_changed = notification_email != current_notification_email
    if notification_changed:
        config.setdefault("email", {})["to"] = notification_email
        env_updates["EMAIL_TO"] = notification_email
        changed_items.append("notification email")

    if not changed_items:
        return JSONResponse(
            {
                "message": "No account changes detected.",
                "login_email": current_login_email,
                "notification_email": current_notification_email,
                "reauth_required": False,
            }
        )

    try:
        _update_env_file(env_updates)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update .env: {exc}") from exc

    _apply_runtime_env(env_updates)

    if notification_changed:
        save_config(config)

    reauth_required = ("login email" in changed_items) or ("password" in changed_items)
    if reauth_required:
        global SESSION_VERSION
        with SESSION_VERSION_LOCK:
            SESSION_VERSION = bump_session_version()
        LOGGER.info("User account credentials changed — all sessions invalidated")

    if len(changed_items) == 1:
        message = f"Updated {changed_items[0]}."
    else:
        message = f"Updated {', '.join(changed_items[:-1])} and {changed_items[-1]}."
    if reauth_required:
        message += " You will be redirected to sign in again."

    return JSONResponse(
        {
            "message": message,
            "login_email": login_email,
            "notification_email": notification_email,
            "reauth_required": reauth_required,
        }
    )


@APP.post("/api/settings/login-email")
@APP.post("/api/settings/username")
async def update_dashboard_login_email(request: Request) -> JSONResponse:
    session = require_session(request)
    require_csrf(request, session)

    payload = await request.json()
    new_login_email = _normalize_email(
        payload.get("new_login_email", "") or payload.get("new_username", ""),
    )

    if not _is_valid_email(new_login_email):
        raise HTTPException(status_code=400, detail="Enter a valid login email address")

    env_updates = {
        "DASHBOARD_LOGIN_EMAIL": new_login_email,
        "DASHBOARD_USERNAME": "",
        "RECOVERY_EMAIL": new_login_email,
    }

    try:
        _update_env_file(env_updates)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update .env: {exc}") from exc

    _apply_runtime_env(env_updates)

    global SESSION_VERSION
    with SESSION_VERSION_LOCK:
        SESSION_VERSION = bump_session_version()
    LOGGER.info("Login email changed to '%s' — all sessions invalidated", new_login_email)

    return JSONResponse({"message": "Login email updated. You will be redirected to sign in again."})


@APP.post("/api/settings/notification-email")
@APP.post("/api/settings/user-email")
async def update_notification_email(request: Request) -> JSONResponse:
    session = require_session(request)
    require_csrf(request, session)

    payload = await request.json()
    email = _normalize_email(payload.get("email", ""))

    if not _is_valid_email(email):
        raise HTTPException(status_code=400, detail="Enter a valid notification email address")

    env_updates = {"EMAIL_TO": email}

    config = load_config()
    config.setdefault("email", {})["to"] = email
    save_config(config)

    try:
        _update_env_file(env_updates)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update .env: {exc}") from exc

    _apply_runtime_env(env_updates)

    return JSONResponse({"message": "Notification email saved. Used for alerts and weekly reports."})


def _contract_metric_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"avg": 0.0, "min": 0.0, "max": 0.0}
    return {
        "avg": round(sum(values) / len(values), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


def _contract_period_identity(config: dict, start_str: str, end_str: str) -> dict[str, str]:
    fallback = _detected_account_network_identity(config)
    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        return fallback

    entries = load_measurement_entries_in_range(config, start_date, end_date)
    if not entries:
        return fallback

    latest = entries[-1]
    return {
        "provider": str(latest.get("isp", "") or fallback["provider"]),
        "ip_address": str(latest.get("ip_address", "") or fallback["ip_address"]),
    }


def _contract_summary(config: dict, start_str: str, end_str: str) -> dict:
    """Build performance summary for a date range (contract period)."""
    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d")
    except ValueError:
        return {"error": "Invalid start date"}
    try:
        end_date = datetime.strptime(end_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        return {"error": "Invalid end date"}

    filtered = load_measurement_entries_in_range(config, start_date, end_date)
    thresholds = config.get("thresholds", {})

    if not filtered:
        return {
            "start_date": start_str,
            "end_date": end_str,
            "total_tests": 0,
            "sources": {"scheduled": 0, "manual": 0},
            "breaches": {"download": 0, "upload": 0, "ping": 0, "loss": 0, "total": 0},
            "message": "No speed test data found for this period.",
        }

    downloads = [e["download_mbps"] for e in filtered]
    uploads = [e["upload_mbps"] for e in filtered]
    pings = [e["ping_ms"] for e in filtered]
    jitters = [e["jitter_ms"] for e in filtered]
    packet_losses = [e["packet_loss_percent"] for e in filtered]
    manual_tests = sum(1 for entry in filtered if str(entry.get("source", "")).lower() == "manual")
    scheduled_tests = len(filtered) - manual_tests
    download_breaches = sum(
        1
        for entry in filtered
        if _safe_float(entry.get("download_mbps"), 0.0)
        < _safe_float(thresholds.get("download_mbps"), 0.0)
    )
    upload_breaches = sum(
        1
        for entry in filtered
        if _safe_float(entry.get("upload_mbps"), 0.0)
        < _safe_float(thresholds.get("upload_mbps"), 0.0)
    )
    ping_breaches = sum(
        1
        for entry in filtered
        if _safe_float(entry.get("ping_ms"), 0.0)
        > _safe_float(thresholds.get("ping_ms"), 9_999_999.0)
    )
    loss_breaches = sum(
        1
        for entry in filtered
        if _safe_float(entry.get("packet_loss_percent"), 0.0)
        > _safe_float(thresholds.get("packet_loss_percent"), 9_999_999.0)
    )
    latest_timestamp = max(
        (
            entry.get("timestamp")
            for entry in filtered
            if isinstance(entry.get("timestamp"), datetime)
        ),
        default=None,
    )

    return {
        "start_date": start_str,
        "end_date": end_str,
        "total_tests": len(filtered),
        "latest_test_at": latest_timestamp.strftime("%Y-%m-%d %H:%M") if latest_timestamp else "",
        "download": _contract_metric_stats(downloads),
        "upload": _contract_metric_stats(uploads),
        "ping": _contract_metric_stats(pings),
        "jitter": _contract_metric_stats(jitters),
        "packet_loss": _contract_metric_stats(packet_losses),
        "jitter_avg": _contract_metric_stats(jitters)["avg"],
        "packet_loss_avg": _contract_metric_stats(packet_losses)["avg"],
        "sources": {"scheduled": scheduled_tests, "manual": manual_tests},
        "breaches": {
            "download": download_breaches,
            "upload": upload_breaches,
            "ping": ping_breaches,
            "loss": loss_breaches,
            "total": download_breaches + upload_breaches + ping_breaches + loss_breaches,
        },
    }


def _resolved_contract_entry(config: dict, entry: dict) -> dict:
    start_str = str(entry.get("start_date", "") or "")
    end_str = str(entry.get("end_date", "") or "")
    detected_identity = _contract_period_identity(config, start_str, end_str)
    account_cfg = config.get("account", {})

    return {
        **entry,
        "provider": str(entry.get("provider", "") or detected_identity["provider"]),
        "account_name": str(entry.get("account_name", "") or account_cfg.get("name", "")),
        "account_number": str(entry.get("account_number", "") or account_cfg.get("number", "")),
        "ip_address": str(entry.get("ip_address", "") or detected_identity["ip_address"]),
        "summary": entry.get("summary") or (_contract_summary(config, start_str, end_str) if start_str and end_str else None),
    }


def _send_contract_report_email(config: dict, archived: dict) -> tuple[bool, str]:
    summary = archived.get("summary") or {}
    subject = (
        f"SpeedPulse Contract Summary - "
        f"{archived.get('provider') or 'Archived contract'} "
        f"({archived.get('start_date') or '?'} to {archived.get('end_date') or '?'})"
    )
    body_html = build_contract_report_html(config, archived, summary)

    try:
        mail = load_mail_settings(config)
    except Exception as exc:
        LOGGER.warning("Contract archive email skipped: %s", exc)
        return False, str(exc)

    message = MIMEMultipart("alternative")
    message["From"] = mail.from_addr
    message["To"] = mail.to_addr
    message["Subject"] = subject
    message.attach(
        MIMEText(
            (
                "SpeedPulse contract summary\n\n"
                f"Provider: {archived.get('provider') or 'N/A'}\n"
                f"Period: {archived.get('start_date') or '?'} to {archived.get('end_date') or '?'}\n"
                f"Tests: {summary.get('total_tests', 0)}\n"
            ),
            "plain",
            "utf-8",
        )
    )
    message.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        server: smtplib.SMTP | smtplib.SMTP_SSL
        if mail.smtp_port == 465:
            server = smtplib.SMTP_SSL(mail.smtp_server, mail.smtp_port, timeout=60)
        else:
            server = smtplib.SMTP(mail.smtp_server, mail.smtp_port, timeout=60)
            server.starttls()

        with server:
            server.login(mail.smtp_username, mail.smtp_password)
            server.send_message(message)

        LOGGER.info("Contract summary email sent for %s", subject)
        return True, "Contract summary report emailed successfully."
    except Exception as exc:
        LOGGER.exception("Failed to send contract summary email")
        return False, str(exc)


@APP.get("/api/contract/summary")
def contract_summary(request: Request) -> JSONResponse:
    require_session(request)
    config = load_config()
    contract_cfg = config.get("contract", {})
    current = contract_cfg.get("current", {})
    history = contract_cfg.get("history", [])

    start_str = current.get("start_date", "")
    end_str = current.get("end_date", "")

    result: dict = {
        "current": _resolved_contract_entry(
            config,
            {
                **current,
                "summary": _contract_summary(config, start_str, end_str) if start_str and end_str else None,
            },
        ),
        "history": [],
    }

    for past in history:
        result["history"].append(_resolved_contract_entry(config, past))

    return JSONResponse(result)


@APP.post("/api/contract/end")
async def end_current_contract(request: Request) -> JSONResponse:
    session = require_session(request)
    require_csrf(request, session)

    config = load_config()
    contract_cfg = config.setdefault("contract", {})
    current = contract_cfg.get("current", {})
    history = contract_cfg.setdefault("history", [])

    start_str = current.get("start_date", "")
    end_str = current.get("end_date", "")

    if not start_str or not end_str:
        raise HTTPException(status_code=400, detail="Current contract has no start/end dates set")

    detected_identity = _contract_period_identity(config, start_str, end_str)
    account_cfg = config.get("account", {})
    summary = _contract_summary(config, start_str, end_str)
    archived = {
        **current,
        "provider": detected_identity["provider"],
        "ip_address": detected_identity["ip_address"],
        "account_name": str(account_cfg.get("name", "")),
        "account_number": str(account_cfg.get("number", "")),
        "summary": summary,
        "archived_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    history.append(archived)

    contract_cfg["current"] = {
        "start_date": "",
        "end_date": "",
        "download_mbps": 0,
        "upload_mbps": 0,
    }

    save_config(config)

    email_sent, email_message = _send_contract_report_email(config, archived)

    return JSONResponse({
        "message": "Contract ended and archived.",
        "archived": _resolved_contract_entry(config, archived),
        "email": {
            "sent": email_sent,
            "message": email_message,
        },
    })
