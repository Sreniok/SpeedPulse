#!/usr/bin/env python3
"""FastAPI dashboard with session-based login for speed test monitoring."""

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
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

from log_parser import load_all_log_entries
from mail_settings import load_mail_settings

APP = FastAPI(title="Speed Monitor Dashboard", version="1.1.0")
SCRIPT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
ENV_PATH = SCRIPT_DIR / ".env"
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
APP.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

LOGGER = logging.getLogger("speed-monitor.web")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SESSION_COOKIE = "speedtest_session"
FLASH_COOKIE = "speedtest_flash"
AUTH_SALT = os.getenv("AUTH_SALT", "")

FAILED_LOGINS: dict[str, list[float]] = {}
BLOCKED_UNTIL: dict[str, float] = {}
AUTH_LOCK = threading.Lock()
SESSION_VERSION = 1
SESSION_VERSION_LOCK = threading.Lock()

MANUAL_SPEEDTEST_LOCK = threading.Lock()
LAST_MANUAL_SPEEDTEST_AT = 0.0
MANUAL_RUN_STATE_LOCK = threading.Lock()
MANUAL_RUN_STATE = {
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
SERVER_OPTIONS_CACHE_TTL_SECONDS = 600
SERVER_OPTIONS_CACHE = {"fetched_at": 0.0, "options": []}


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


def _update_manual_run_state(**changes: object) -> None:
    with MANUAL_RUN_STATE_LOCK:
        MANUAL_RUN_STATE.update(changes)
        MANUAL_RUN_STATE["updated_at"] = _iso_now()


def _append_manual_run_log(line: str) -> None:
    with MANUAL_RUN_STATE_LOCK:
        logs = list(MANUAL_RUN_STATE.get("logs", []))
        logs.append(line)
        MANUAL_RUN_STATE["logs"] = logs[-14:]
        MANUAL_RUN_STATE["updated_at"] = _iso_now()


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
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


CONFIG_LOCK = threading.Lock()


def save_config(config: dict) -> None:
    with CONFIG_LOCK:
        with CONFIG_PATH.open("w", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            json.dump(config, handle, indent=2)
            handle.write("\n")


def _clean_env_value(value: str) -> str:
    return str(value).replace("\n", "").replace("\r", "").strip()


def _update_env_file(updates: dict[str, str]) -> None:
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    positions: dict[str, int] = {}
    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            continue
        key = raw_line.split("=", 1)[0].strip()
        if key:
            positions[key] = index

    for key, value in updates.items():
        sanitized = _clean_env_value(value)
        escaped = sanitized.replace("\\", "\\\\").replace('"', '\\"')
        line = f'{key}="{escaped}"'
        if key in positions:
            lines[positions[key]] = line
        else:
            lines.append(line)

    ENV_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _apply_runtime_env(updates: dict[str, str]) -> None:
    for key, value in updates.items():
        os.environ[key] = _clean_env_value(value)


def build_password_hash(password: str, iterations: int = 390000) -> str:
    salt_hex = secrets.token_hex(16)
    digest = hash_password_pbkdf2(password, salt_hex, iterations)
    return f"pbkdf2_sha256:{iterations}:{salt_hex}:{digest}"


def dashboard_settings_payload(config: dict | None = None) -> dict:
    loaded = config or load_config()
    account_cfg = loaded.get("account", {})
    email_cfg = loaded.get("email", {})
    notifications_cfg = loaded.get("notifications", {})
    scheduling_cfg = loaded.get("scheduling", {})

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

    return {
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
    }


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
        "This is a test email from Speed Monitor settings.\n\nIf you received this, SMTP setup is working.",
        "plain",
        "utf-8",
    )
    msg["From"] = mail.from_addr
    msg["To"] = mail.to_addr
    msg["Subject"] = "Speed Monitor Test Notification"

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
    if hostname.startswith("10.") or hostname.startswith("192.168."):
        pass  # Private LAN is acceptable for self-hosted webhook receivers
    if hostname.endswith(".internal") or hostname.endswith(".local"):
        pass  # mDNS / internal DNS is common in home lab setups


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
            "title": "Speed Monitor test notification",
            "message": "Webhook channel test from dashboard settings.",
            "timestamp": _iso_now(),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "speed-monitor/1.1"},
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
    payload = f"Speed Monitor test notification ({_iso_now()})".encode("utf-8")
    request = urllib.request.Request(
        target_url,
        data=payload,
        method="POST",
        headers={
            "Title": "Speed Monitor Test",
            "Priority": "3",
            "Tags": "satellite,white_check_mark",
            "User-Agent": "speed-monitor/1.1",
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
    if not AUTH_SALT:
        raise RuntimeError("AUTH_SALT must be set (use a random hex string, e.g. python3 -c 'import secrets; print(secrets.token_hex(16))')")

    secret_key = os.getenv("APP_SECRET_KEY", "")
    if not secret_key or secret_key in {"change-me", "replace-with-long-random-secret"} or len(secret_key) < 32:
        raise RuntimeError("APP_SECRET_KEY must be set to a strong random value (minimum 32 characters)")

    username = os.getenv("DASHBOARD_USERNAME", "").strip()
    if not username:
        raise RuntimeError("DASHBOARD_USERNAME must be set")

    password_hash = os.getenv("DASHBOARD_PASSWORD_HASH", "").strip()
    password_plain = os.getenv("DASHBOARD_PASSWORD", "").strip()

    if password_hash:
        if not _validate_password_hash_format(password_hash):
            raise RuntimeError("DASHBOARD_PASSWORD_HASH format is invalid")
    else:
        # Backward compatibility path, but still enforce non-default value.
        if not password_plain:
            raise RuntimeError("Set DASHBOARD_PASSWORD_HASH (recommended) or DASHBOARD_PASSWORD")
        if password_plain in {"change-me", "admin", "password"}:
            raise RuntimeError("DASHBOARD_PASSWORD must not use default/insecure value")
        LOGGER.warning("Plain DASHBOARD_PASSWORD detected — auto-hashing and persisting as DASHBOARD_PASSWORD_HASH.")
        new_hash = build_password_hash(password_plain)
        env_updates = {"DASHBOARD_PASSWORD_HASH": new_hash, "DASHBOARD_PASSWORD": ""}
        try:
            _update_env_file(env_updates)
        except OSError:
            LOGGER.warning("Could not persist DASHBOARD_PASSWORD_HASH to .env (read-only filesystem?)")
        _apply_runtime_env(env_updates)


def get_serializer() -> URLSafeSerializer:
    return URLSafeSerializer(os.getenv("APP_SECRET_KEY", ""), salt=AUTH_SALT)


def _extract_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _is_login_blocked(client_ip: str) -> int:
    now = time.time()
    with AUTH_LOCK:
        blocked_until = BLOCKED_UNTIL.get(client_ip, 0)
        if blocked_until <= now:
            BLOCKED_UNTIL.pop(client_ip, None)
            return 0
        return int(blocked_until - now)


def _register_failed_login(client_ip: str) -> int:
    max_attempts = _env_int("LOGIN_MAX_ATTEMPTS", 5)
    window_seconds = _env_int("LOGIN_WINDOW_SECONDS", 900)
    block_seconds = _env_int("LOGIN_BLOCK_SECONDS", 900)

    now = time.time()
    with AUTH_LOCK:
        recent = [attempt for attempt in FAILED_LOGINS.get(client_ip, []) if now - attempt <= window_seconds]
        recent.append(now)
        FAILED_LOGINS[client_ip] = recent

        if len(recent) >= max_attempts:
            BLOCKED_UNTIL[client_ip] = now + block_seconds
            FAILED_LOGINS[client_ip] = []
            return block_seconds

    return 0


def _clear_failed_logins(client_ip: str) -> None:
    with AUTH_LOCK:
        FAILED_LOGINS.pop(client_ip, None)
        BLOCKED_UNTIL.pop(client_ip, None)


def verify_login_credentials(username: str, password: str) -> bool:
    expected_user = os.getenv("DASHBOARD_USERNAME", "")
    password_hash = os.getenv("DASHBOARD_PASSWORD_HASH", "").strip()
    password_plain = os.getenv("DASHBOARD_PASSWORD", "").strip()

    user_ok = hmac.compare_digest(username, expected_user)
    if not user_ok:
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

    username = payload.get("username")
    exp = payload.get("exp")
    csrf = payload.get("csrf")

    if not username or not exp or not csrf:
        return None

    if int(exp) < int(time.time()):
        return None

    with SESSION_VERSION_LOCK:
        required_version = SESSION_VERSION
    token_version = payload.get("sv", 1)
    if token_version < required_version:
        return None

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
        "last_test_at": last_test_at,
        "range_label": "Today" if mode == "today" else f"Last {days} days",
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


@APP.on_event("startup")
def on_startup() -> None:
    validate_security_configuration()
    LOGGER.info("Web security configuration validated")


@APP.get("/health")
def health() -> dict:
    return {"status": "ok", "time": datetime.now().isoformat()}


def _set_flash(response: RedirectResponse, message: str) -> RedirectResponse:
    """Set a short-lived signed flash cookie for displaying login errors."""
    signed = get_serializer().dumps({"msg": message, "t": int(time.time())})
    response.set_cookie(
        key=FLASH_COOKIE,
        value=signed,
        httponly=True,
        samesite="strict",
        max_age=60,
        path="/login",
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
    error = _consume_flash(request)
    response = TEMPLATES.TemplateResponse("login.html", {"request": request, "error": error})
    response.delete_cookie(FLASH_COOKIE, path="/login")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@APP.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)) -> RedirectResponse:
    client_ip = _extract_client_ip(request)
    blocked_for = _is_login_blocked(client_ip)
    if blocked_for > 0:
        response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
        return _set_flash(response, f"Too many attempts. Retry in {blocked_for}s")

    if not verify_login_credentials(username, password):
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
    token = get_serializer().dumps({"username": username, "exp": exp_ts, "csrf": csrf_token, "sv": sv})

    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="strict",
        secure=os.getenv("SESSION_COOKIE_SECURE", "true").lower() != "false",
        max_age=ttl_seconds,
    )
    return response


@APP.get("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(SESSION_COOKIE)
    return response


@APP.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request) -> HTMLResponse:
    session = current_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    config = load_config()
    account = config.get("account", {})

    response = TEMPLATES.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "username": session["username"],
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
        "settings.html",
        {
            "request": request,
            "username": session["username"],
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
    email_to = _clean_env_value(payload.get("email_to", ""))
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

    if not smtp_server or not smtp_username or not email_from or not email_to:
        raise HTTPException(status_code=400, detail="SMTP server, username, from, and to are required")

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
    email_cfg["to"] = email_to
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

    save_config(config)

    env_updates = {
        "SMTP_SERVER": smtp_server,
        "SMTP_PORT": str(smtp_port),
        "SMTP_USERNAME": smtp_username,
        "EMAIL_FROM": email_from,
        "EMAIL_TO": email_to,
    }
    if smtp_password.strip():
        env_updates["SMTP_PASSWORD"] = smtp_password

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

    if not verify_login_credentials(str(session.get("username", "")), current_password):
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
        SESSION_VERSION += 1
    LOGGER.info("Session version bumped — all existing sessions invalidated after password change")

    return JSONResponse({"message": "Dashboard password updated. You will be redirected to login."})


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
