"""Tests for SQL-backed measurement storage and legacy log import."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from db_migrate import import_logs
from measurement_store import (
    delete_app_secret,
    get_app_secret,
    has_app_secret,
    list_notification_events,
    list_speed_tests,
    record_notification_event,
    record_speed_test,
    run_migrations,
    set_app_secret,
)

LOG_FIXTURE = """\
Date: 13-03-2026
Time: 08:00
Source: scheduled
Server: London
ISP: Example ISP
IP: 203.0.113.10
Ping: 12 ms
Jitter: 1 ms
Packet Loss: 0%
Download: 610 Mbps
Upload: 95 Mbps

Date: 13-03-2026
Time: 16:00
Source: manual
Server: Manchester
ISP: Example ISP
IP: 203.0.113.10
Ping: 31 ms
Jitter: 3 ms
Packet Loss: 1.2%
Download: 420 Mbps
Upload: 70 Mbps
"""


def test_record_speed_test_and_query_results():
    run_migrations()

    stored = record_speed_test(
        {
            "timestamp": datetime(2026, 3, 13, 8, 0),
            "source": "scheduled",
            "server": "London",
            "server_id": "1234",
            "isp": "Example ISP",
            "ip_address": "203.0.113.10",
            "download_mbps": 610.0,
            "upload_mbps": 95.0,
            "ping_ms": 12.0,
            "jitter_ms": 1.0,
            "packet_loss_percent": 0.0,
            "result_url": "https://example.test/result",
        }
    )

    assert stored is True
    rows = list_speed_tests()
    assert len(rows) == 1
    assert rows[0]["server"] == "London"
    assert rows[0]["download_mbps"] == 610.0


def test_import_logs_populates_speed_tests(tmp_path: Path):
    run_migrations()
    log_dir = tmp_path / "Log"
    log_dir.mkdir()
    (log_dir / "speed_log_week_11.txt").write_text(LOG_FIXTURE, encoding="utf-8")

    config = {
        "paths": {
            "log_directory": str(log_dir),
        }
    }

    summary = import_logs(config)

    assert summary["entries"] == 2
    assert summary["inserted"] == 2
    rows = list_speed_tests()
    assert len(rows) == 2
    assert rows[0]["source"] == "scheduled"
    assert rows[1]["source"] == "manual"


def test_notification_events_are_stored():
    run_migrations()

    assert record_notification_event("email", "weekly_report", "Week 11 report sent") is True
    events = list_notification_events(limit=10)

    assert len(events) == 1
    assert events[0]["channel"] == "email"
    assert events[0]["event_type"] == "weekly_report"


def test_app_secrets_are_encrypted_and_round_trip():
    applied = run_migrations()

    assert "002_encrypted_app_secrets" in applied
    assert set_app_secret("smtp_password", "super-secret-pass") is True
    assert has_app_secret("smtp_password") is True
    assert get_app_secret("smtp_password") == "super-secret-pass"
    assert delete_app_secret("smtp_password") is True
    assert has_app_secret("smtp_password") is False
