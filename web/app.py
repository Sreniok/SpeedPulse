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
import shutil
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
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
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
from log_parser import load_all_log_entries
from mail_settings import load_mail_settings
from version import USER_AGENT, __version__
from state_store import (
    blocked_seconds as state_blocked_seconds,
    bump_session_version,
    clear_login_failures as state_clear_login_failures,
    consume_reset_token as state_consume_reset_token,
    get_session_version,
    initialize_state_store,
    load_manual_runtime_state,
    register_failed_login as state_register_failed_login,
    save_manual_runtime_state,
    store_reset_token,
)

SCRIPT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
ENV_PATH = SCRIPT_DIR / ".env"
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
TEMPLATES.env.globals["app_version"] = __version__

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


def _resolve_runtime_path(default_path: Path, env_name: str) -> Path:
    raw_value = os.getenv(env_name, "").strip()
    if not raw_value:
        return default_path
    path = Path(raw_value)
    if path.is_absolute():
        return path
    return SCRIPT_DIR / path


def _config_path() -> Path:
    return _resolve_runtime_path(CONFIG_PATH, "CONFIG_PATH")


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


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


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
        MANUAL_RUN_STATE["logs"] = logs[-14:]
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


def dashboard_settings_payload(config: dict | None = None) -> dict:
    loaded = config or load_config()
    account_cfg = loaded.get("account", {})
    email_cfg = loaded.get("email", {})
    notifications_cfg = loaded.get("notifications", {})
    scheduling_cfg = loaded.get("scheduling", {})
    backup_cfg = loaded.get("backup", {})

    smtp_port_raw = os.getenv("SMTP_PORT", str(email_cfg.get("smtp_port", 465)))
    try:
        smtp_port = int(smtp_port_raw)
    except ValueError:
        smtp_port = 465

    smtp_username = os.getenv("SMTP_USERNAME", email_cfg.get("from", ""))
    email_from = os.getenv("EMAIL_FROM", email_cfg.get("from", smtp_username))

    contract_cfg = loaded.get("contract", {})
    current_contract = contract_cfg.get("current", {})
    contract_history = contract_cfg.get("history", [])
    login_email = _resolve_login_email(loaded)
    notification_email = _resolve_notification_email(loaded)

    return {
        "login_email": login_email,
        "notification_email": notification_email,
        "username": login_email,
        "user_email": notification_email,
        "account": {
            "name": str(account_cfg.get("name", "")),
            "number": str(account_cfg.get("number", "")),
            "provider": str(account_cfg.get("provider", "")),
        },
        "server_selection_id": str(loaded.get("speedtest", {}).get("server_id", "") or ""),
        "email": {
            "smtp_server": os.getenv("SMTP_SERVER", email_cfg.get("smtp_server", "")),
            "smtp_port": smtp_port,
            "smtp_username": smtp_username,
            "smtp_password_set": bool(os.getenv("SMTP_PASSWORD", "").strip()),
            "from": email_from,
            "to": os.getenv("EMAIL_TO", email_cfg.get("to", "")),
            "send_realtime_alerts": bool(email_cfg.get("send_realtime_alerts", True)),
        },
        "notifications": {
            "weekly_report_enabled": bool(notifications_cfg.get("weekly_report_enabled", True)),
            "weekly_report_time": scheduling_cfg.get("weekly_report_time", "Monday 08:00"),
            "test_times": scheduling_cfg.get("test_times", ["08:00", "16:00", "22:00"]),
            "webhook_enabled": bool(notifications_cfg.get("webhook_enabled", False)),
            "webhook_url": str(notifications_cfg.get("webhook_url", "")),
            "ntfy_enabled": bool(notifications_cfg.get("ntfy_enabled", False)),
            "ntfy_server": str(notifications_cfg.get("ntfy_server", "https://ntfy.sh")),
            "ntfy_topic": str(notifications_cfg.get("ntfy_topic", "")),
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


def resolve_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return SCRIPT_DIR / path


def resolve_speedtest_executable(config: dict) -> str:
    configured = config.get("paths", {}).get("speedtest_exe", "speedtest")
    candidates = [
        configured,
        "speedtest",
        "speedtest-cli",
        "/usr/bin/speedtest",
        "/usr/local/bin/speedtest",
        "/usr/bin/speedtest-cli",
        "/usr/local/bin/speedtest-cli",
    ]

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        resolved = shutil.which(candidate)
        if resolved:
            return resolved

    raise FileNotFoundError("No speedtest executable found in PATH")


def _parse_server_listing(output: str) -> list[dict]:
    options: list[dict] = []
    pattern = re.compile(r"^\s*(\d+)\s+(.+?)\s{2,}(.+?)\s{2,}(.+?)\s*$")

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if not line or line.startswith("Closest servers") or line.startswith("=") or line.lstrip().startswith("ID "):
            continue

        match = pattern.match(line)
        if not match:
            continue

        server_id, name, location, country = match.groups()
        options.append(
            {
                "id": server_id,
                "name": name.strip(),
                "location": location.strip(),
                "country": country.strip(),
                "label": f"{name.strip()} - {location.strip()}",
            }
        )

    return options


def get_speedtest_server_options(force_refresh: bool = False) -> list[dict]:
    now = time.time()
    cached_at = float(SERVER_OPTIONS_CACHE.get("fetched_at", 0.0) or 0.0)
    cached_options = SERVER_OPTIONS_CACHE.get("options", [])
    if not force_refresh and cached_options and (now - cached_at) < SERVER_OPTIONS_CACHE_TTL_SECONDS:
        return list(cached_options)

    config = load_config()
    speedtest_exe = resolve_speedtest_executable(config)
    result = subprocess.run(
        [speedtest_exe, "--servers"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Unable to list speedtest servers").strip())

    options = _parse_server_listing(result.stdout)
    SERVER_OPTIONS_CACHE["fetched_at"] = now
    SERVER_OPTIONS_CACHE["options"] = list(options)
    return options


def current_server_setting(config: dict | None = None) -> str:
    loaded = config or load_config()
    return str(loaded.get("speedtest", {}).get("server_id", "") or "").strip()


def server_setting_payload(config: dict | None = None, force_refresh: bool = False) -> dict:
    loaded = config or load_config()
    selected_id = current_server_setting(loaded)

    options = []
    try:
        options = get_speedtest_server_options(force_refresh=force_refresh)
    except Exception:
        LOGGER.exception("Failed to load speedtest server list")

    selected_label = "Auto (nearest server)"
    normalized_options = [
        {
            "id": "",
            "label": "Auto (nearest server)",
            "name": "Auto",
            "location": "Automatic",
            "country": "",
        }
    ]

    found_selected = not selected_id
    for option in options:
        normalized_options.append(option)
        if option["id"] == selected_id:
            selected_label = option["label"]
            found_selected = True

    if selected_id and not found_selected:
        selected_label = f"Pinned server #{selected_id}"
        normalized_options.append(
            {
                "id": selected_id,
                "label": selected_label,
                "name": "Pinned server",
                "location": f"ID {selected_id}",
                "country": "",
            }
        )

    return {
        "selected_id": selected_id,
        "selected_label": selected_label,
        "options": normalized_options,
    }


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
    log_dir = resolve_path(config.get("paths", {}).get("log_directory", "Log"))
    entries = load_all_log_entries(log_dir)
    now = datetime.now()
    scheduled_tests_per_day = len(scheduling.get("test_times", []))
    today_entries = [entry for entry in entries if entry["timestamp"].date() == now.date()]

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
        },
        "timeseries": timeseries,
        "latest_tests": latest_entries,
        "incidents": incidents,
        "last_test_at": last_test_at,
        "range_label": "Today" if mode == "today" else f"Last {days} days",
        "sla": sla,
        "server_selection_id": server_settings["selected_id"],
        "server_selection_label": server_settings["selected_label"],
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


@APP.get("/api/notifications/log")
async def api_notification_log(request: Request):
    require_session(request)
    from state_store import get_notification_log
    entries = get_notification_log(limit=50)
    return entries


@APP.get("/health")
def health() -> dict:
    return {"status": "ok", "time": datetime.now().isoformat()}


def _set_flash(response: RedirectResponse, message: str, path: str = "/login") -> RedirectResponse:
    """Set a short-lived signed flash cookie for displaying errors."""
    signed = get_serializer().dumps({"msg": message, "t": int(time.time())})
    response.set_cookie(
        key=FLASH_COOKIE,
        value=signed,
        httponly=True,
        samesite="strict",
        max_age=60,
        path=path,
    )
    return response


def _consume_flash(request: Request) -> str | None:
    """Read and validate flash cookie. Returns message or None."""
    token = request.cookies.get(FLASH_COOKIE)
    if not token:
        return None
    try:
        payload = get_serializer().loads(token)
    except BadSignature:
        return None
    issued = payload.get("t", 0)
    if int(time.time()) - int(issued) > 60:
        return None
    return str(payload.get("msg", ""))


@APP.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    setup_mode = _is_setup_mode()
    recovery_email = _resolve_recovery_email()
    error = _consume_flash(request)
    response = TEMPLATES.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "error": error,
            "setup_mode": setup_mode,
            "has_recovery_email": bool(recovery_email) and not setup_mode,
        },
    )
    response.delete_cookie(FLASH_COOKIE, path="/login")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@APP.post("/login")
def login(
    request: Request,
    email: str = Form(""),
    username: str = Form(""),
    password: str = Form(...),
) -> RedirectResponse:
    if _is_setup_mode():
        return RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)

    login_email = _normalize_email(email or username)
    client_ip = _extract_client_ip(request)
    blocked_for = _is_login_blocked(client_ip)
    if blocked_for > 0:
        response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
        return _set_flash(response, f"Too many attempts. Retry in {blocked_for}s")

    if not verify_login_credentials(login_email, password):
        new_block_seconds = _register_failed_login(client_ip)
        if new_block_seconds > 0:
            LOGGER.warning("Login blocked for %ss from IP %s", new_block_seconds, client_ip)
            response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
            return _set_flash(response, f"Too many attempts. Retry in {new_block_seconds}s")

        LOGGER.warning("Failed login attempt from IP %s", client_ip)
        response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
        return _set_flash(response, "Invalid credentials")

    _clear_failed_logins(client_ip)

    ttl_seconds = _env_int("SESSION_TTL_SECONDS", 60 * 60 * 12)
    exp_ts = int(time.time()) + ttl_seconds
    csrf_token = secrets.token_urlsafe(24)
    with SESSION_VERSION_LOCK:
        sv = SESSION_VERSION
    token = get_serializer().dumps(
        {
            "login_email": login_email,
            "username": login_email,
            "exp": exp_ts,
            "csrf": csrf_token,
            "sv": sv,
        }
    )

    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="strict",
        secure=_is_secure_request(request),
        max_age=ttl_seconds,
    )
    return response


@APP.get("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ── Registration (setup mode only) ──────────────────────────────


@APP.get("/register", response_class=HTMLResponse)
def register_page(request: Request) -> HTMLResponse:
    if not _is_setup_mode():
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    error = _consume_flash(request)
    response = TEMPLATES.TemplateResponse(request, "register.html", {"request": request, "error": error})
    response.delete_cookie(FLASH_COOKIE, path="/register")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@APP.post("/register")
def register(
    request: Request,
    email: str = Form(""),
    username: str = Form(""),
    password: str = Form(...),
    confirm_password: str = Form(...),
) -> RedirectResponse:
    if not _is_setup_mode():
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    login_email = _normalize_email(email or username)
    if not _is_valid_email(login_email):
        response = RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)
        return _set_flash(response, "Enter a valid login email address", path="/register")

    if len(password) < 10:
        response = RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)
        return _set_flash(response, "Password must be at least 10 characters", path="/register")

    if password != confirm_password:
        response = RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)
        return _set_flash(response, "Passwords do not match", path="/register")

    password_hash = build_password_hash(password)
    env_updates = {
        "DASHBOARD_LOGIN_EMAIL": login_email,
        "DASHBOARD_USERNAME": "",
        "RECOVERY_EMAIL": login_email,
        "DASHBOARD_PASSWORD_HASH": password_hash,
        "DASHBOARD_PASSWORD": "",
    }

    try:
        _update_env_file(env_updates)
    except OSError as exc:
        LOGGER.error("Failed to write credentials to .env: %s", exc)
        response = RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)
        return _set_flash(response, "Failed to save credentials", path="/register")

    _apply_runtime_env(env_updates)
    LOGGER.info("Account created for login email '%s' via setup wizard", login_email)

    # Auto-login the new user
    ttl_seconds = _env_int("SESSION_TTL_SECONDS", 60 * 60 * 12)
    exp_ts = int(time.time()) + ttl_seconds
    csrf_token = secrets.token_urlsafe(24)
    with SESSION_VERSION_LOCK:
        sv = SESSION_VERSION
    token = get_serializer().dumps(
        {
            "login_email": login_email,
            "username": login_email,
            "exp": exp_ts,
            "csrf": csrf_token,
            "sv": sv,
        }
    )

    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="strict",
        secure=_is_secure_request(request),
        max_age=ttl_seconds,
    )
    return response


# ── Forgot / Reset password ─────────────────────────────────────


@APP.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request) -> HTMLResponse:
    if _is_setup_mode():
        return RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)

    error = _consume_flash(request)
    response = TEMPLATES.TemplateResponse(
        request,
        "forgot_password.html",
        {"request": request, "error": error, "sent": False},
    )
    response.delete_cookie(FLASH_COOKIE, path="/forgot-password")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@APP.post("/forgot-password")
def forgot_password(request: Request, email: str = Form(""), username: str = Form("")) -> HTMLResponse:
    if _is_setup_mode():
        return RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)

    login_email = _normalize_email(email or username)
    client_ip = _extract_client_ip(request)
    blocked_for = _is_login_blocked(client_ip)
    if blocked_for > 0:
        return TEMPLATES.TemplateResponse(
            request,
            "forgot_password.html",
            {"request": request, "error": f"Too many attempts. Retry in {blocked_for}s", "sent": False},
        )

    # Always show success to prevent login email enumeration
    success_response = TEMPLATES.TemplateResponse(
        request,
        "forgot_password.html",
        {"request": request, "error": None, "sent": True},
    )

    recovery_email = _resolve_recovery_email()
    expected_login_email = _resolve_login_email()

    if not recovery_email or not hmac.compare_digest(login_email, expected_login_email):
        _register_failed_login(client_ip)
        LOGGER.info("Forgot-password request — no action (email mismatch or no recovery email)")
        return success_response

    try:
        token = _create_reset_token(login_email)
        base_url = str(request.base_url)
        _send_reset_email(recovery_email, token, base_url)
        LOGGER.info("Password reset email sent for login email '%s'", login_email)
    except Exception as exc:
        LOGGER.error("Failed to send password reset email: %s", exc)

    return success_response


@APP.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request) -> HTMLResponse:
    if _is_setup_mode():
        return RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)

    token = request.query_params.get("token", "")
    error = _consume_flash(request)
    response = TEMPLATES.TemplateResponse(
        request,
        "reset_password.html",
        {"request": request, "token": token, "error": error, "success": False},
    )
    response.delete_cookie(FLASH_COOKIE, path="/reset-password")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@APP.post("/reset-password")
def reset_password(
    request: Request,
    token: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
) -> HTMLResponse:
    if _is_setup_mode():
        return RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)

    if len(new_password) < 10:
        response = RedirectResponse(url=f"/reset-password?token={quote(token)}", status_code=status.HTTP_302_FOUND)
        response.set_cookie(key=FLASH_COOKIE, value=get_serializer().dumps({"msg": "Password must be at least 10 characters", "t": int(time.time())}), httponly=True, samesite="strict", max_age=60, path="/reset-password")
        return response

    if new_password != confirm_password:
        response = RedirectResponse(url=f"/reset-password?token={quote(token)}", status_code=status.HTTP_302_FOUND)
        response.set_cookie(key=FLASH_COOKIE, value=get_serializer().dumps({"msg": "Passwords do not match", "t": int(time.time())}), httponly=True, samesite="strict", max_age=60, path="/reset-password")
        return response

    login_email = _consume_reset_token(token)
    if not login_email:
        return TEMPLATES.TemplateResponse(
            request,
            "reset_password.html",
            {"request": request, "token": "", "error": "Reset link is invalid or has expired. Please request a new one.", "success": False},
        )

    new_hash = build_password_hash(new_password)
    env_updates = {"DASHBOARD_PASSWORD_HASH": new_hash, "DASHBOARD_PASSWORD": ""}

    try:
        _update_env_file(env_updates)
    except OSError as exc:
        LOGGER.error("Failed to persist password reset: %s", exc)
        return TEMPLATES.TemplateResponse(
            request,
            "reset_password.html",
            {"request": request, "token": "", "error": "Failed to save new password.", "success": False},
        )

    _apply_runtime_env(env_updates)

    global SESSION_VERSION
    with SESSION_VERSION_LOCK:
        SESSION_VERSION = bump_session_version()
    LOGGER.info("Password reset completed for login email '%s' — all sessions invalidated", login_email)

    return TEMPLATES.TemplateResponse(
        request,
        "reset_password.html",
        {"request": request, "token": "", "error": None, "success": True},
    )


@APP.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request) -> HTMLResponse:
    session = current_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    config = load_config()
    account = config.get("account", {})

    response = TEMPLATES.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "login_email": session["login_email"],
            "account_name": account.get("name", "N/A"),
            "account_number": account.get("number", "N/A"),
            "account_provider": account.get("provider", ""),
            "csrf_token": session["csrf"],
        },
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@APP.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    session = current_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    config = load_config()
    account = config.get("account", {})

    response = TEMPLATES.TemplateResponse(
        request,
        "settings.html",
        {
            "request": request,
            "login_email": session["login_email"],
            "account_name": account.get("name", "N/A"),
            "account_number": account.get("number", "N/A"),
            "account_provider": account.get("provider", ""),
            "csrf_token": session["csrf"],
        },
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@APP.get("/api/metrics")
def metrics(request: Request, days: int = 30, mode: str = "days") -> JSONResponse:
    require_session(request)
    if mode not in {"days", "today"}:
        raise HTTPException(status_code=400, detail="mode must be 'days' or 'today'")
    if mode == "days" and (days < 1 or days > 365):
        raise HTTPException(status_code=400, detail="days must be between 1 and 365")

    payload = build_dashboard_payload(days, mode=mode)
    return JSONResponse(payload)


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
    test_times = _normalize_test_times(payload.get("test_times", ["08:00", "16:00", "22:00"]))
    selected_server_id = _clean_env_value(payload.get("server_id", ""))

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

    config = load_config()
    account_cfg = config.setdefault("account", {})
    email_cfg = config.setdefault("email", {})
    scheduling_cfg = config.setdefault("scheduling", {})
    notifications_cfg = config.setdefault("notifications", {})
    speedtest_cfg = config.setdefault("speedtest", {})

    account_cfg["name"] = account_name
    account_cfg["provider"] = broadband_provider
    account_cfg["number"] = broadband_account_number

    email_cfg["smtp_server"] = smtp_server
    email_cfg["smtp_port"] = smtp_port
    email_cfg["from"] = email_from
    email_cfg["send_realtime_alerts"] = bool(payload.get("send_realtime_alerts", True))

    scheduling_cfg["test_times"] = test_times
    scheduling_cfg["weekly_report_time"] = weekly_report_time
    speedtest_cfg["server_id"] = selected_server_id

    notifications_cfg["weekly_report_enabled"] = bool(payload.get("weekly_report_enabled", True))
    notifications_cfg["webhook_enabled"] = bool(payload.get("webhook_enabled", False))
    notifications_cfg["webhook_url"] = _clean_env_value(payload.get("webhook_url", ""))
    notifications_cfg["ntfy_enabled"] = bool(payload.get("ntfy_enabled", False))
    notifications_cfg["ntfy_server"] = _clean_env_value(payload.get("ntfy_server", "https://ntfy.sh")) or "https://ntfy.sh"
    notifications_cfg["ntfy_topic"] = _clean_env_value(payload.get("ntfy_topic", ""))

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
    }
    if smtp_password.strip():
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


@APP.post("/api/settings/login-email")
@APP.post("/api/settings/username")
async def update_dashboard_login_email(request: Request) -> JSONResponse:
    session = require_session(request)
    require_csrf(request, session)

    payload = await request.json()
    current_password = str(payload.get("current_password", "") or "")
    new_login_email = _normalize_email(
        payload.get("new_login_email", "") or payload.get("new_username", ""),
    )

    if not current_password:
        raise HTTPException(status_code=400, detail="Current password is required to change login email")

    if not _is_valid_email(new_login_email):
        raise HTTPException(status_code=400, detail="Enter a valid login email address")

    if not verify_login_credentials(str(session.get("login_email", "")), current_password):
        raise HTTPException(status_code=403, detail="Current password is incorrect")

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

    log_dir = resolve_path(config.get("paths", {}).get("log_directory", "Log"))
    entries = load_all_log_entries(log_dir)
    filtered = [e for e in entries if start_date <= e["timestamp"] <= end_date]

    if not filtered:
        return {
            "start_date": start_str,
            "end_date": end_str,
            "total_tests": 0,
            "message": "No speed test data found for this period.",
        }

    downloads = [e["download_mbps"] for e in filtered]
    uploads = [e["upload_mbps"] for e in filtered]
    pings = [e["ping_ms"] for e in filtered]
    jitters = [e["jitter_ms"] for e in filtered]
    packet_losses = [e["packet_loss_percent"] for e in filtered]

    def avg(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    return {
        "start_date": start_str,
        "end_date": end_str,
        "total_tests": len(filtered),
        "download": {
            "avg": avg(downloads),
            "min": round(min(downloads), 2),
            "max": round(max(downloads), 2),
        },
        "upload": {
            "avg": avg(uploads),
            "min": round(min(uploads), 2),
            "max": round(max(uploads), 2),
        },
        "ping": {
            "avg": avg(pings),
            "min": round(min(pings), 2),
            "max": round(max(pings), 2),
        },
        "jitter_avg": avg(jitters),
        "packet_loss_avg": avg(packet_losses),
    }


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
        "current": {
            **current,
            "summary": _contract_summary(config, start_str, end_str) if start_str and end_str else None,
        },
        "history": [],
    }

    for past in history:
        s = past.get("start_date", "")
        e = past.get("end_date", "")
        result["history"].append({
            **past,
            "summary": past.get("summary") or (_contract_summary(config, s, e) if s and e else None),
        })

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

    summary = _contract_summary(config, start_str, end_str)
    archived = {**current, "summary": summary}
    history.append(archived)

    contract_cfg["current"] = {
        "start_date": "",
        "end_date": "",
        "download_mbps": 0,
        "upload_mbps": 0,
    }

    save_config(config)

    return JSONResponse({
        "message": "Contract ended and archived.",
        "archived": archived,
    })


@APP.get("/api/run/speedtest/status")
def speedtest_run_status(request: Request) -> JSONResponse:
    require_session(request)
    cooldown_seconds = _env_int("MANUAL_SPEEDTEST_COOLDOWN_SECONDS", 300)
    remaining = max(0, int((LAST_MANUAL_SPEEDTEST_AT + cooldown_seconds) - time.time()))

    payload = _manual_run_snapshot()
    payload["cooldown_remaining_seconds"] = remaining
    return JSONResponse(payload)


@APP.post("/api/run/speedtest")
async def run_speedtest_now(request: Request) -> JSONResponse:
    global LAST_MANUAL_SPEEDTEST_AT

    session = require_session(request)
    require_csrf(request, session)

    payload = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()

    selected_id = str(payload.get("server_id", "") or "").strip()
    if selected_id and not selected_id.isdigit():
        raise HTTPException(status_code=400, detail="server_id must be numeric or empty")

    config = load_config()
    selected_label = _resolve_server_label(selected_id, config=config)

    cooldown_seconds = _env_int("MANUAL_SPEEDTEST_COOLDOWN_SECONDS", 300)
    now = time.time()
    remaining = int((LAST_MANUAL_SPEEDTEST_AT + cooldown_seconds) - now)
    if remaining > 0:
        return JSONResponse(
            {
                "status": "cooldown",
                "message": f"Manual speed test cooldown active. Retry in {remaining}s.",
                "cooldown_remaining_seconds": remaining,
            },
            status_code=429,
        )

    if not MANUAL_SPEEDTEST_LOCK.acquire(blocking=False):
        payload = _manual_run_snapshot()
        payload["message"] = payload.get("message") or "A speed test is already running."
        return JSONResponse(payload, status_code=409)

    LAST_MANUAL_SPEEDTEST_AT = now
    _start_manual_run_state(selected_server_id=selected_id, selected_server_label=selected_label)

    worker = threading.Thread(
        target=_manual_speedtest_worker,
        kwargs={"selected_server_id": selected_id},
        name="manual-speedtest",
        daemon=True,
    )
    try:
        worker.start()
    except Exception:
        MANUAL_SPEEDTEST_LOCK.release()
        _update_manual_run_state(
            status="failed",
            stage="Failed",
            message="Unable to start manual speed test worker.",
            completed_at=_iso_now(),
            exit_code=-1,
        )
        LOGGER.exception("Failed to start manual speed test worker")
        return JSONResponse(_manual_run_snapshot(), status_code=500)

    payload = _manual_run_snapshot()
    payload["message"] = "Manual speed test started."
    return JSONResponse(payload, status_code=202)


# ── Backup & Restore ────────────────────────────────────────────

_MAX_BACKUP_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB


@APP.post("/api/backup/create")
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
    encrypted, filename = create_backup(password, include_logs=include_logs)
    try:
        dest = save_backup_to_path(encrypted, filename, config)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Backup created but could not be saved to disk: {exc}",
        ) from exc

    if not download:
        return JSONResponse({
            "message": "Backup saved to the configured backup directory.",
            "filename": dest.name,
            "size_bytes": len(encrypted),
        })

    return Response(
        content=encrypted,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@APP.get("/api/backup/list")
async def api_backup_list(request: Request):
    require_session(request)
    config = load_config()
    return JSONResponse({"backups": list_backups(config)})


@APP.get("/api/backup/download/{filename}")
async def api_backup_download(request: Request, filename: str):
    require_session(request)
    config = load_config()
    path = get_backup_path(filename, config)
    if path is None:
        raise HTTPException(status_code=404, detail="Backup not found.")
    data = path.read_bytes()
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@APP.post("/api/backup/preview")
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
    if len(data) > _MAX_BACKUP_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Backup file is too large (max 100 MB).")

    try:
        manifest = validate_backup(data, password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return JSONResponse({"manifest": manifest})


@APP.post("/api/backup/restore")
async def api_backup_restore(request: Request):
    session = require_session(request)
    require_csrf(request, session)

    form = await request.form()
    password = str(form.get("password", "")).strip()
    current_password = str(form.get("current_password", "")).strip()
    upload = form.get("file")

    if not password:
        raise HTTPException(status_code=400, detail="Backup password is required.")
    if not current_password:
        raise HTTPException(status_code=400, detail="Current dashboard password is required to confirm restore.")

    # Verify the user's current dashboard password
    stored_hash = os.getenv("DASHBOARD_PASSWORD_HASH", "").strip()
    if not stored_hash or not verify_password(current_password, stored_hash):
        raise HTTPException(status_code=403, detail="Current dashboard password is incorrect.")

    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=400, detail="No backup file uploaded.")

    data = await upload.read()
    if len(data) > _MAX_BACKUP_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Backup file is too large (max 100 MB).")

    try:
        summary = restore_backup(data, password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    LOGGER.info("Backup restored: %s", summary.get("restored", []))
    return JSONResponse({
        "message": "Backup restored successfully. Restart the application to apply all changes.",
        "restored": summary.get("restored", []),
        "warnings": summary.get("warnings", []),
    })


@APP.delete("/api/backup/{filename}")
async def api_backup_delete(request: Request, filename: str):
    session = require_session(request)
    require_csrf(request, session)

    config = load_config()
    if not delete_backup(filename, config):
        raise HTTPException(status_code=404, detail="Backup not found.")
    return JSONResponse({"message": "Backup deleted."})
