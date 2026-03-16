"""API coverage for dashboard metrics, settings, and manual runs."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backup_manager import validate_backup

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
                "backup": {
                    "backup_directory": str(tmp_path / "Backups"),
                    "max_backups": 10,
                    "scheduled_backup_enabled": False,
                    "scheduled_backup_time": "03:00",
                    "scheduled_backup_frequency": "daily",
                    "scheduled_backup_include_logs": True,
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
    import backup_manager

    monkeypatch.setattr(webapp, "AUTH_SALT", "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")
    monkeypatch.setattr(backup_manager, "SCRIPT_DIR", tmp_path)

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


def test_broadband_threshold_settings_update_metrics_and_alert_thresholds(api_client):
    client, _, config_path, _, csrf_token = api_client

    current_settings = client.get("/api/settings/notifications")
    assert current_settings.status_code == 200
    settings_payload = current_settings.json()

    update_payload = {
        "account_name": settings_payload["account"]["name"],
        "broadband_provider": settings_payload["account"]["provider"],
        "broadband_account_number": settings_payload["account"]["number"],
        "smtp_server": settings_payload["email"]["smtp_server"],
        "smtp_port": settings_payload["email"]["smtp_port"],
        "smtp_username": settings_payload["email"]["smtp_username"],
        "smtp_password": "",
        "email_from": settings_payload["email"]["from"],
        "send_realtime_alerts": settings_payload["email"]["send_realtime_alerts"],
        "weekly_report_enabled": settings_payload["notifications"]["weekly_report_enabled"],
        "weekly_report_time": settings_payload["notifications"]["weekly_report_time"],
        "test_times": settings_payload["notifications"]["test_times"],
        "server_id": settings_payload["server_selection_id"],
        "webhook_enabled": settings_payload["notifications"]["webhook_enabled"],
        "webhook_url": settings_payload["notifications"]["webhook_url"],
        "ntfy_enabled": settings_payload["notifications"]["ntfy_enabled"],
        "ntfy_server": settings_payload["notifications"]["ntfy_server"],
        "ntfy_topic": settings_payload["notifications"]["ntfy_topic"],
        "thresholds": {
            "download_mbps": 555,
            "upload_mbps": 85,
        },
        "contract": settings_payload["contract"],
        "backup": settings_payload["backup"],
    }

    save_response = client.post(
        "/api/settings/notifications",
        headers={"X-CSRF-Token": csrf_token},
        json=update_payload,
    )

    assert save_response.status_code == 200
    saved_payload = save_response.json()
    assert saved_payload["thresholds"]["download_mbps"] == 555.0
    assert saved_payload["thresholds"]["upload_mbps"] == 85.0

    saved_config = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved_config["thresholds"]["download_mbps"] == 555.0
    assert saved_config["thresholds"]["upload_mbps"] == 85.0

    metrics_response = client.get("/api/metrics?mode=days&days=30")
    assert metrics_response.status_code == 200
    metrics_payload = metrics_response.json()
    assert metrics_payload["thresholds"]["download_mbps"] == 555.0
    assert metrics_payload["thresholds"]["upload_mbps"] == 85.0


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


def test_login_email_update_no_longer_requires_current_password(api_client):
    client, _, _, env_path, csrf_token = api_client

    response = client.post(
        "/api/settings/login-email",
        headers={"X-CSRF-Token": csrf_token},
        json={"new_login_email": "newlogin@example.com"},
    )

    assert response.status_code == 200
    assert "Login email updated" in response.json()["message"]
    env_text = env_path.read_text(encoding="utf-8")
    assert 'DASHBOARD_LOGIN_EMAIL="newlogin@example.com"' in env_text

    follow_up = client.get("/api/settings/notifications")
    assert follow_up.status_code == 401


def test_user_account_combined_save_updates_notification_email_only(api_client):
    client, _, config_path, env_path, csrf_token = api_client

    response = client.post(
        "/api/settings/user-account",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "login_email": "testuser@example.com",
            "notification_email": "combined@example.com",
            "current_password": "",
            "new_password": "",
            "confirm_password": "",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reauth_required"] is False
    assert payload["notification_email"] == "combined@example.com"

    saved_config = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved_config["email"]["to"] == "combined@example.com"
    assert 'EMAIL_TO="combined@example.com"' in env_path.read_text(encoding="utf-8")

    still_logged_in = client.get("/api/settings/notifications")
    assert still_logged_in.status_code == 200


def test_user_account_combined_save_updates_login_email_without_password(api_client):
    client, _, _, env_path, csrf_token = api_client

    response = client.post(
        "/api/settings/user-account",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "login_email": "combined-login@example.com",
            "notification_email": "alerts@example.com",
            "current_password": "",
            "new_password": "",
            "confirm_password": "",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reauth_required"] is True
    assert payload["login_email"] == "combined-login@example.com"
    assert (
        'DASHBOARD_LOGIN_EMAIL="combined-login@example.com"'
        in env_path.read_text(encoding="utf-8")
    )

    follow_up = client.get("/api/settings/notifications")
    assert follow_up.status_code == 401


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


def test_manual_backup_create_saves_to_backup_directory(api_client):
    client, _, config_path, _, csrf_token = api_client

    response = client.post(
        "/api/backup/create",
        headers={"X-CSRF-Token": csrf_token},
        json={"password": "testpass123", "include_logs": True, "download": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "saved" in payload["message"].lower()
    filename = payload["filename"]
    assert filename.endswith(".speedpulse-backup")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    backup_dir = Path(config["backup"]["backup_directory"])
    backup_path = backup_dir / filename
    assert backup_path.is_file()
    assert backup_path.stat().st_size == payload["size_bytes"]


def test_manual_backup_saved_file_can_be_downloaded(api_client):
    client, _, config_path, _, csrf_token = api_client

    create_response = client.post(
        "/api/backup/create",
        headers={"X-CSRF-Token": csrf_token},
        json={"password": "testpass123", "include_logs": False, "download": False},
    )

    assert create_response.status_code == 200
    filename = create_response.json()["filename"]

    download_response = client.get(f"/api/backup/download/{filename}")

    assert download_response.status_code == 200
    assert download_response.headers["content-type"] == "application/octet-stream"
    assert f'filename="{filename}"' in download_response.headers["content-disposition"]

    config = json.loads(config_path.read_text(encoding="utf-8"))
    backup_path = Path(config["backup"]["backup_directory"]) / filename
    assert download_response.content == backup_path.read_bytes()


def test_manual_backup_can_save_and_download_in_one_request(api_client):
    client, _, config_path, _, csrf_token = api_client

    response = client.post(
        "/api/backup/create",
        headers={"X-CSRF-Token": csrf_token},
        json={"password": "testpass123", "include_logs": True, "download": True},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    disposition = response.headers["content-disposition"]
    match = disposition.split('filename="', 1)[1].rstrip('"')
    backup_path = Path(json.loads(config_path.read_text(encoding="utf-8"))["backup"]["backup_directory"]) / match
    assert backup_path.is_file()
    assert response.content == backup_path.read_bytes()


def test_manual_backup_uses_saved_scheduler_password_when_blank(
    api_client, monkeypatch: pytest.MonkeyPatch
):
    client, _, config_path, _, csrf_token = api_client
    monkeypatch.setenv("BACKUP_PASSWORD", "schedulerpass123")

    response = client.post(
        "/api/backup/create",
        headers={"X-CSRF-Token": csrf_token},
        json={"password": "", "include_logs": True, "download": False},
    )

    assert response.status_code == 200
    filename = response.json()["filename"]
    backup_path = Path(json.loads(config_path.read_text(encoding="utf-8"))["backup"]["backup_directory"]) / filename
    manifest = validate_backup(backup_path.read_bytes(), "schedulerpass123")
    assert "config.json" in manifest["files"]


def test_manual_backup_allows_one_off_password_override(
    api_client, monkeypatch: pytest.MonkeyPatch
):
    client, _, config_path, _, csrf_token = api_client
    monkeypatch.setenv("BACKUP_PASSWORD", "schedulerpass123")

    response = client.post(
        "/api/backup/create",
        headers={"X-CSRF-Token": csrf_token},
        json={"password": "overridepass123", "include_logs": False, "download": False},
    )

    assert response.status_code == 200
    filename = response.json()["filename"]
    backup_path = Path(json.loads(config_path.read_text(encoding="utf-8"))["backup"]["backup_directory"]) / filename
    manifest = validate_backup(backup_path.read_bytes(), "overridepass123")
    assert manifest["include_logs"] is False

    with pytest.raises(ValueError, match="Wrong backup password"):
        validate_backup(backup_path.read_bytes(), "schedulerpass123")


def test_manual_backup_requires_password_when_no_saved_password(api_client):
    client, _, _, _, csrf_token = api_client

    response = client.post(
        "/api/backup/create",
        headers={"X-CSRF-Token": csrf_token},
        json={"password": "", "include_logs": True, "download": False},
    )

    assert response.status_code == 400
    assert "save one first" in response.json()["detail"]


def test_backup_restore_returns_restart_required(api_client, monkeypatch: pytest.MonkeyPatch):
    client, webapp, _, _, csrf_token = api_client

    monkeypatch.setattr(
        webapp,
        "restore_backup",
        lambda data, password: {"restored": ["config.json"], "warnings": []},
    )

    response = client.post(
        "/api/backup/restore",
        headers={"X-CSRF-Token": csrf_token},
        files={"file": ("restore.speedpulse-backup", b"backup-bytes", "application/octet-stream")},
        data={"password": "restorepass123"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "Backup restored successfully."
    assert payload["restart_required"] is True
    assert payload["restored"] == ["config.json"]


def test_backup_restore_requires_backup_password(api_client):
    client, _, _, _, csrf_token = api_client
    response = client.post(
        "/api/backup/restore",
        headers={"X-CSRF-Token": csrf_token},
        files={"file": ("restore.speedpulse-backup", b"backup-bytes", "application/octet-stream")},
        data={"password": ""},
    )

    assert response.status_code == 400
    assert "required" in response.json()["detail"]
