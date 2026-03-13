"""API coverage for dashboard metrics, settings, and manual runs."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

LOG_FIXTURE = """\
Date: 13-03-2026
Time: 08:00
Server: London
ISP: Example ISP
Ping: 12 ms
Jitter: 1 ms
Packet Loss: 0%
Download: 610 Mbps
Upload: 95 Mbps

Date: 13-03-2026
Time: 16:00
Server: Manchester
ISP: Example ISP
Ping: 31 ms
Jitter: 3 ms
Packet Loss: 1.2%
Download: 420 Mbps
Upload: 70 Mbps

Date: 13-03-2026
Time: 22:00
Server: London
ISP: Example ISP
Ping: 11 ms
Jitter: 1 ms
Packet Loss: 0%
Download: 605 Mbps
Upload: 92 Mbps
"""


@pytest.fixture()
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    log_dir = tmp_path / "Log"
    log_dir.mkdir()
    (log_dir / "speed_log_week_11.txt").write_text(LOG_FIXTURE, encoding="utf-8")

    config_path = tmp_path / "config.json"
    env_path = tmp_path / ".env"
    config_path.write_text(
        json.dumps(
            {
                "account": {"name": "Test Account", "number": "1234", "provider": "Test ISP"},
                "paths": {
                    "speedtest_exe": "speedtest",
                    "log_directory": str(log_dir),
                    "images_directory": str(tmp_path / "Images"),
                    "chart_base64": str(tmp_path / "chart_base64.txt"),
                    "error_log": str(tmp_path / "errors.log"),
                },
                "thresholds": {
                    "download_mbps": 500,
                    "upload_mbps": 80,
                    "ping_ms": 20,
                    "packet_loss_percent": 1.0,
                },
                "email": {
                    "from": "sender@example.com",
                    "to": "alerts@example.com",
                    "smtp_server": "smtp.example.com",
                    "smtp_port": 465,
                },
                "notifications": {
                    "weekly_report_enabled": True,
                    "webhook_enabled": False,
                    "ntfy_enabled": False,
                },
                "scheduling": {
                    "test_times": ["08:00", "16:00", "22:00"],
                    "weekly_report_time": "Friday 18:00",
                },
                "speedtest": {"server_id": ""},
                "contract": {"current": {}, "history": []},
            }
        ),
        encoding="utf-8",
    )
    env_path.write_text('EMAIL_TO="alerts@example.com"\n', encoding="utf-8")

    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    monkeypatch.setenv("ENV_PATH", str(env_path))
    monkeypatch.setenv("AUTH_SALT", "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")
    monkeypatch.setenv(
        "APP_SECRET_KEY",
        "test-secret-key-that-is-long-enough-for-validation-1234567890",
    )
    monkeypatch.setenv("DASHBOARD_LOGIN_EMAIL", "testuser@example.com")
    monkeypatch.setenv("DASHBOARD_USERNAME", "")
    monkeypatch.setenv(
        "DASHBOARD_PASSWORD_HASH",
        "pbkdf2_sha256:260000:salt:hash",
    )
    monkeypatch.setenv("EMAIL_TO", "alerts@example.com")

    import web.app as webapp

    monkeypatch.setattr(webapp, "AUTH_SALT", "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")

    with TestClient(webapp.APP, raise_server_exceptions=False) as client:
        csrf_token = "csrf-token-for-tests"
        token = webapp.get_serializer().dumps(
            {
                "login_email": "testuser@example.com",
                "username": "testuser@example.com",
                "exp": int(time.time()) + 3600,
                "csrf": csrf_token,
                "sv": webapp.SESSION_VERSION,
            }
        )
        client.cookies.set(webapp.SESSION_COOKIE, token)
        yield client, webapp, config_path, env_path, csrf_token

    if webapp.MANUAL_SPEEDTEST_LOCK.locked():
        webapp.MANUAL_SPEEDTEST_LOCK.release()
    with webapp.MANUAL_RUN_STATE_LOCK:
        webapp.MANUAL_RUN_STATE = dict(webapp.DEFAULT_MANUAL_RUN_STATE)
    webapp.LAST_MANUAL_SPEEDTEST_AT = 0.0
    webapp._persist_manual_runtime_state()


def test_metrics_payload_includes_sla_and_incidents(api_client):
    client, _, _, _, _ = api_client

    response = client.get("/api/metrics?mode=days&days=30")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_tests"] == 3
    assert payload["sla"]["grade"] == "F"
    assert payload["sla"]["breach_tests"] == 1
    assert payload["sla"]["incident_count"] == 1
    assert len(payload["incidents"]) == 1
    assert payload["incidents"][0]["headline"] == "Download below floor / Upload below floor / Ping above ceiling"
    assert payload["incidents"][0]["primary_server"] == "Manchester"


def test_notification_email_settings_persist_to_config_and_env(api_client):
    client, _, config_path, env_path, csrf_token = api_client

    response = client.post(
        "/api/settings/notification-email",
        headers={"X-CSRF-Token": csrf_token},
        json={"email": "weekly@example.com"},
    )

    assert response.status_code == 200
    assert "Notification email saved" in response.json()["message"]
    saved_config = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved_config["email"]["to"] == "weekly@example.com"
    assert 'EMAIL_TO="weekly@example.com"' in env_path.read_text(encoding="utf-8")


def test_server_selection_settings_persist_to_config(api_client):
    client, _, config_path, _, csrf_token = api_client

    response = client.post(
        "/api/settings/server",
        headers={"X-CSRF-Token": csrf_token},
        json={"server_id": "41075"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_id"] == "41075"
    saved_config = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved_config["speedtest"]["server_id"] == "41075"


def test_manual_speedtest_start_and_status_endpoint(api_client, monkeypatch: pytest.MonkeyPatch):
    client, webapp, _, _, csrf_token = api_client

    class DummyThread:
        def __init__(self, target=None, kwargs=None, name=None, daemon=None):
            self.target = target
            self.kwargs = kwargs or {}
            self.name = name
            self.daemon = daemon

        def start(self):
            return None

    monkeypatch.setattr(webapp.threading, "Thread", DummyThread)
    monkeypatch.setattr(
        webapp,
        "_resolve_server_label",
        lambda server_id, config=None: f"Pinned server #{server_id}" if server_id else "Auto (nearest server)",
    )

    response = client.post(
        "/api/run/speedtest",
        headers={"X-CSRF-Token": csrf_token},
        json={"server_id": "41075"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["selected_server_id"] == "41075"

    status_response = client.get("/api/run/speedtest/status")
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["status"] == "running"
    assert status_payload["selected_server_id"] == "41075"
