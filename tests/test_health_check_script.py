from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

measurement_store_stub = types.ModuleType("measurement_store")
measurement_store_stub.get_app_secret = lambda _name: ""
measurement_store_stub.list_speed_tests = lambda *args, **kwargs: []
measurement_store_stub.record_notification_event = lambda *args, **kwargs: None
sys.modules.setdefault("measurement_store", measurement_store_stub)


def _health_check_module():
    return importlib.import_module("health_check")


def _freeze_now(monkeypatch: pytest.MonkeyPatch, frozen_now: datetime) -> None:
    health_check = _health_check_module()

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now

    monkeypatch.setattr(health_check, "datetime", FrozenDateTime)


def _build_config(log_dir: Path, **scheduling_overrides) -> dict:
    scheduling = {
        "scan_enabled": True,
        "scan_frequency": "daily",
        "test_times": ["08:30", "16:30", "22:30"],
        "scan_weekly_day": "Monday",
        "scan_monthly_day": 1,
        "scan_custom_days": [1],
    }
    scheduling.update(scheduling_overrides)
    return {
        "paths": {
            "log_directory": str(log_dir),
            "images_directory": str(log_dir.parent / "Images"),
            "error_log": str(log_dir.parent / "errors.log"),
        },
        "scheduling": scheduling,
        "speedtest": {
            "max_retries": 3,
            "retry_delay_seconds": 30,
            "timeout_seconds": 120,
        },
    }


def _entry(timestamp: datetime, source: str = "scheduled") -> dict:
    return {
        "timestamp": timestamp,
        "source": source,
    }


def test_check_log_files_does_not_require_current_week_file(tmp_path: Path) -> None:
    health_check = _health_check_module()
    log_dir = tmp_path / "Log"
    log_dir.mkdir()
    (log_dir / "speed_log_week_15.txt").write_text("historical log\n", encoding="utf-8")

    result = health_check.check_log_files(_build_config(log_dir))

    assert result["healthy"] is True
    assert all("Current week's log file" not in issue for issue in result["issues"])


def test_daily_schedule_before_first_monday_run_uses_previous_due_slot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    health_check = _health_check_module()
    log_dir = tmp_path / "Log"
    log_dir.mkdir()
    config = _build_config(log_dir)
    _freeze_now(monkeypatch, datetime(2026, 4, 13, 7, 0))
    monkeypatch.setattr(
        health_check,
        "load_measurement_entries",
        lambda _config: [_entry(datetime(2026, 4, 12, 22, 30))],
    )

    result = health_check.check_last_speedtest(config)

    assert result["healthy"] is True
    assert result["issue"] is None
    assert result["last_test"] == "2026-04-12 22:30:00"


def test_daily_schedule_reports_a_missed_due_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    health_check = _health_check_module()
    log_dir = tmp_path / "Log"
    log_dir.mkdir()
    config = _build_config(log_dir)
    _freeze_now(monkeypatch, datetime(2026, 4, 13, 10, 0))
    monkeypatch.setattr(
        health_check,
        "load_measurement_entries",
        lambda _config: [_entry(datetime(2026, 4, 12, 22, 30))],
    )

    result = health_check.check_last_speedtest(config)

    assert result["healthy"] is False
    assert result["issue"] == "Scheduled speed test due at 2026-04-13 08:30 has not completed"


@pytest.mark.parametrize(
    ("scheduling_overrides", "frozen_now", "last_scheduled"),
    [
        (
            {"scan_frequency": "weekly", "scan_weekly_day": "Monday", "test_times": ["08:30"]},
            datetime(2026, 4, 13, 7, 0),
            datetime(2026, 4, 6, 8, 30),
        ),
        (
            {"scan_frequency": "monthly", "scan_monthly_day": 13, "test_times": ["08:30"]},
            datetime(2026, 4, 13, 7, 0),
            datetime(2026, 3, 13, 8, 30),
        ),
        (
            {"scan_frequency": "custom", "scan_custom_days": [1, 13], "test_times": ["08:30"]},
            datetime(2026, 4, 13, 7, 0),
            datetime(2026, 4, 1, 8, 30),
        ),
    ],
)
def test_non_daily_schedules_use_the_latest_due_slot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scheduling_overrides: dict,
    frozen_now: datetime,
    last_scheduled: datetime,
) -> None:
    health_check = _health_check_module()
    log_dir = tmp_path / "Log"
    log_dir.mkdir()
    config = _build_config(log_dir, **scheduling_overrides)
    _freeze_now(monkeypatch, frozen_now)
    monkeypatch.setattr(
        health_check,
        "load_measurement_entries",
        lambda _config: [_entry(last_scheduled)],
    )

    result = health_check.check_last_speedtest(config)

    assert result["healthy"] is True
    assert result["issue"] is None
