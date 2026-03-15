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


def test_login_shows_create_account_cta_in_setup_mode(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("DASHBOARD_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)

    response = client.get("/login")

    assert response.status_code == 200
    assert "Create Account" in response.text
    assert "No administrator account exists yet" in response.text
    assert 'action="/login"' not in response.text


def test_register_saves_hash_and_login_works(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    """Full setup-mode flow: register → credentials saved → login succeeds."""
    import web.app as webapp

    # Start in setup mode — no credentials at all
    monkeypatch.delenv("DASHBOARD_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.delenv("DASHBOARD_LOGIN_EMAIL", raising=False)
    monkeypatch.delenv("DASHBOARD_USERNAME", raising=False)
    monkeypatch.setattr(webapp, "AUTH_SALT", "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4")

    # Point .env writes at a temp file so we can inspect them
    env_file = tmp_path / ".env"
    env_file.write_text(
        'DASHBOARD_LOGIN_EMAIL=""\nDASHBOARD_PASSWORD_HASH=""\nDASHBOARD_PASSWORD=""\n'
    )
    monkeypatch.setattr(webapp, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(webapp, "ENV_PATH", env_file)

    with TestClient(webapp.APP, raise_server_exceptions=False) as tc:
        # 1) Register
        reg = tc.post(
            "/register",
            data={
                "email": "new@example.com",
                "password": "strongpass123",
                "confirm_password": "strongpass123",
            },
            follow_redirects=False,
        )
        assert reg.status_code == 302, f"Expected redirect, got {reg.status_code}"

        # 2) Verify hash was written to .env
        env_content = env_file.read_text()
        assert "DASHBOARD_PASSWORD_HASH=" in env_content
        assert "pbkdf2_sha256:390000:" in env_content
        assert 'DASHBOARD_LOGIN_EMAIL="new@example.com"' in env_content

        # 3) Login with the same credentials must succeed
        login = tc.post(
            "/login",
            data={"email": "new@example.com", "password": "strongpass123"},
            follow_redirects=False,
        )
        assert login.status_code == 302
        assert login.headers.get("location") in ("/", "http://testserver/")


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
