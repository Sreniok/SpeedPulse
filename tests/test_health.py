"""Smoke test for the FastAPI /health endpoint."""

from __future__ import annotations

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
def client(_auth_env, monkeypatch: pytest.MonkeyPatch):
    """Create a TestClient after setting required env vars.

    We need to reload the module so it picks up the monkeypatched env.
    """
    # Patch AUTH_SALT at module level before the startup event runs.
    import web.app as webapp
    monkeypatch.setattr(webapp, "AUTH_SALT", "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")

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

    def test_no_auth_required(self, client: TestClient):
        """The health endpoint must be accessible without a session cookie."""
        resp = client.get("/health")
        assert resp.status_code == 200
