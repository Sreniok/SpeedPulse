"""Regression tests for login and password recovery visibility."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_SALT", "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")
    monkeypatch.setenv(
        "APP_SECRET_KEY",
        "test-secret-key-that-is-long-enough-for-validation-1234567890",
    )
    monkeypatch.setenv("DASHBOARD_LOGIN_EMAIL", "testuser@example.com")
    monkeypatch.setenv("DASHBOARD_USERNAME", "")
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", "pbkdf2_sha256:260000:salt:hash")
    monkeypatch.delenv("RECOVERY_EMAIL", raising=False)
    monkeypatch.delenv("EMAIL_TO", raising=False)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    import web.app as webapp

    monkeypatch.setattr(webapp, "AUTH_SALT", "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")
    with TestClient(webapp.APP, raise_server_exceptions=False) as tc:
        yield tc


def test_login_shows_forgot_password_link_when_email_to_exists(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("DASHBOARD_LOGIN_EMAIL", raising=False)
    monkeypatch.setenv("EMAIL_TO", "dest@example.com")

    response = client.get("/login")

    assert response.status_code == 200
    assert "Forgot password?" in response.text


def test_forgot_password_uses_email_to_as_recovery_fallback(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    import web.app as webapp

    sent = {}
    monkeypatch.delenv("DASHBOARD_LOGIN_EMAIL", raising=False)
    monkeypatch.setenv("EMAIL_TO", "dest@example.com")
    monkeypatch.setattr(webapp, "_create_reset_token", lambda login_email: "test-token")

    def fake_send_reset_email(to_addr: str, token: str, base_url: str) -> None:
        sent["to_addr"] = to_addr
        sent["token"] = token
        sent["base_url"] = base_url

    monkeypatch.setattr(webapp, "_send_reset_email", fake_send_reset_email)

    response = client.post("/forgot-password", data={"email": "dest@example.com"})
    normalized_html = " ".join(response.text.split())

    assert response.status_code == 200
    assert sent["to_addr"] == "dest@example.com"
    assert sent["token"] == "test-token"
    assert "reset link has been sent" in normalized_html


def test_dashboard_settings_payload_separates_login_and_notification_emails(
    monkeypatch: pytest.MonkeyPatch,
):
    import web.app as webapp

    monkeypatch.setenv("DASHBOARD_LOGIN_EMAIL", "signin@example.com")
    monkeypatch.setenv("EMAIL_TO", "alerts@example.com")

    payload = webapp.dashboard_settings_payload(
        {
            "account": {},
            "email": {},
            "notifications": {},
            "scheduling": {},
            "contract": {},
        }
    )

    assert payload["login_email"] == "signin@example.com"
    assert payload["notification_email"] == "alerts@example.com"
