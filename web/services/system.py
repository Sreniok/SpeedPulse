"""System-oriented web services: readiness checks and speedtest server discovery."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

SERVER_OPTIONS_CACHE_TTL_SECONDS = 600
SERVER_OPTIONS_CACHE: dict[str, object] = {"fetched_at": 0.0, "options": []}


def resolve_path(path_value: str, runtime_root: Callable[[], Path]) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return runtime_root() / path


def _probe_writable_path(path: Path, *, is_directory: bool) -> None:
    target = path if is_directory else path.parent
    target.mkdir(parents=True, exist_ok=True)
    probe_path = target / f".speedpulse-ready-{os.getpid()}-{time.time_ns()}"
    probe_path.write_text("ok", encoding="utf-8")
    probe_path.unlink(missing_ok=True)


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
            return str(resolved)

    raise FileNotFoundError("No speedtest executable found in PATH")


def build_readiness_state(
    *,
    config_path: Callable[[], Path],
    load_config: Callable[[], dict],
    runtime_root: Callable[[], Path],
    get_state_db_path: Callable[[], Path],
    database_healthcheck: Callable[[], dict[str, str]],
    database_enabled: Callable[[], bool],
    load_mail_settings: Callable[[dict], object],
    resolve_speedtest_executable_fn: Callable[[dict], str] | None = None,
) -> tuple[list[str], list[str], dict[str, str]]:
    failures: list[str] = []
    warnings: list[str] = []
    checks: dict[str, str] = {}

    resolved_config_path = config_path()
    if not resolved_config_path.is_file():
        failures.append(f"Missing config file: {resolved_config_path}")
        checks["config"] = "missing"
        return failures, warnings, checks

    try:
        config = load_config()
        checks["config"] = "ok"
    except Exception as exc:
        failures.append(f"Failed to load config: {exc}")
        checks["config"] = "error"
        return failures, warnings, checks

    storage_targets = {
        "log_directory": resolve_path(config.get("paths", {}).get("log_directory", "Log"), runtime_root),
        "images_directory": resolve_path(config.get("paths", {}).get("images_directory", "Images"), runtime_root),
        "archive_directory": resolve_path("Archive", runtime_root),
        "backup_directory": resolve_path(config.get("backup", {}).get("backup_directory", "Backups"), runtime_root),
        "state_db": get_state_db_path(),
    }
    for name, path in storage_targets.items():
        try:
            _probe_writable_path(path, is_directory=name != "state_db")
            checks[name] = "ok"
        except OSError as exc:
            failures.append(f"Storage path is not writable ({name}): {path} ({exc})")
            checks[name] = "error"

    speedtest_resolver = resolve_speedtest_executable_fn or resolve_speedtest_executable
    try:
        speedtest_resolver(config)
        checks["speedtest_binary"] = "ok"
    except Exception as exc:
        failures.append(f"Speedtest executable unavailable: {exc}")
        checks["speedtest_binary"] = "error"

    db_health = database_healthcheck()
    checks["measurements_database"] = db_health["status"]
    if database_enabled() and db_health["status"] != "ok":
        failures.append(f"Measurement database unavailable: {db_health['message']}")

    notifications = config.get("notifications", {})
    email_cfg = config.get("email", {})
    email_required = bool(
        email_cfg.get("send_realtime_alerts", True)
        or notifications.get("weekly_report_enabled", True)
        or notifications.get("monthly_report_enabled", False)
    )
    if email_required:
        try:
            load_mail_settings(config)
            checks["email_settings"] = "ok"
        except Exception as exc:
            warnings.append(f"Email notifications configured but mail settings are incomplete: {exc}")
            checks["email_settings"] = "warning"
    else:
        checks["email_settings"] = "skipped"

    return failures, warnings, checks


def parse_server_listing(output: str) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
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


def get_speedtest_server_options(
    *,
    load_config: Callable[[], dict],
    force_refresh: bool = False,
) -> list[dict[str, str]]:
    now = time.time()
    cached_at_raw = SERVER_OPTIONS_CACHE.get("fetched_at", 0.0)
    cached_at = float(cached_at_raw) if isinstance(cached_at_raw, int | float) else 0.0
    cached_options = SERVER_OPTIONS_CACHE.get("options", [])
    if not force_refresh and isinstance(cached_options, list) and cached_options and (now - cached_at) < SERVER_OPTIONS_CACHE_TTL_SECONDS:
        return [dict(option) for option in cached_options if isinstance(option, dict)]

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

    options = parse_server_listing(result.stdout)
    SERVER_OPTIONS_CACHE["fetched_at"] = now
    SERVER_OPTIONS_CACHE["options"] = [dict(option) for option in options]
    return options


def current_server_setting(*, load_config: Callable[[], dict], config: dict | None = None) -> str:
    loaded = config or load_config()
    return str(loaded.get("speedtest", {}).get("server_id", "") or "").strip()


def server_setting_payload(
    *,
    load_config: Callable[[], dict],
    logger: logging.Logger,
    config: dict | None = None,
    force_refresh: bool = False,
) -> dict[str, object]:
    loaded = config or load_config()
    selected_id = current_server_setting(load_config=load_config, config=loaded)

    options: list[dict[str, str]] = []
    try:
        options = get_speedtest_server_options(load_config=load_config, force_refresh=force_refresh)
    except Exception:
        logger.exception("Failed to load speedtest server list")

    selected_label = "Auto (nearest server)"
    normalized_options: list[dict[str, str]] = [
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
