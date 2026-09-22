"""Smoke test for the FastAPI /health endpoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def _auth_env(monkeypatch: pytest.MonkeyPatch):
    """Set the minimum env vars required for the app to start."""
    monkeypatch.setenv("AUTH_SALT", "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-key-that-is-long-enough-for-validation-1234567890")
    monkeypatch.setenv("DASHBOARD_LOGIN_EMAIL", "testuser@example.com")
    monkeypatch.setenv("DASHBOARD_USERNAME", "")
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", "pbkdf2_sha256:260000:salt:hash")


@pytest.fixture()
def client(_auth_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Create a TestClient after setting required env vars.

    We need to reload the module so it picks up the monkeypatched env.
    """
    log_dir = tmp_path / "Log"
    images_dir = tmp_path / "Images"
    archive_dir = tmp_path / "Archive"
    backups_dir = tmp_path / "Backups"
    log_dir.mkdir()
    images_dir.mkdir()
    archive_dir.mkdir()
    backups_dir.mkdir()

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "paths": {
                    "speedtest_exe": "speedtest",
                    "log_directory": str(log_dir),
                    "images_directory": str(images_dir),
                    "chart_base64": str(tmp_path / "chart_base64.txt"),
                    "error_log": str(tmp_path / "errors.log"),
                },
                "backup": {"backup_directory": str(backups_dir)},
                "email": {
                    "from": "sender@example.com",
                    "to": "alerts@example.com",
                    "smtp_server": "smtp.example.com",
                    "smtp_port": 465,
                    "send_realtime_alerts": False,
                },
                "notifications": {
                    "weekly_report_enabled": False,
                    "monthly_report_enabled": False,
                },
                "speedtest": {"server_id": ""},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_PATH", str(config_path))

    # Patch AUTH_SALT at module level before the startup event runs.
    import web.app as webapp

    monkeypatch.setattr(webapp, "AUTH_SALT", "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")
    monkeypatch.setattr(webapp, "resolve_speedtest_executable", lambda _config: "/usr/bin/speedtest")

    with TestClient(webapp.APP, raise_server_exceptions=False) as tc:
        yield tc


class TestHealthEndpoint:
    def test_returns_200(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_response_body(self, client: TestClient):
        data = client.get("/health").json()
        assert data["status"] == "ok"
        assert "time" in data
        assert data["service"] == "speedpulse-dashboard"

    def test_no_auth_required(self, client: TestClient):
        """The health endpoint must be accessible without a session cookie."""
        resp = client.get("/health")
        assert resp.status_code == 200


class TestReadinessEndpoint:
    def test_returns_200_when_runtime_is_ready(self, client: TestClient):
        resp = client.get("/ready")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["status"] == "ready"
        assert payload["checks"]["config"] == "ok"
        assert payload["checks"]["speedtest_binary"] == "ok"

    def test_returns_503_when_config_is_missing(self, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        missing_config = tmp_path / "missing-config.json"
        monkeypatch.setenv("CONFIG_PATH", str(missing_config))

        resp = client.get("/ready")
        assert resp.status_code == 503
        payload = resp.json()
        assert payload["status"] == "not_ready"
        assert payload["checks"]["config"] == "missing"
