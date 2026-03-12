"""Unit tests for mail_settings.load_mail_settings()."""

from __future__ import annotations

import pytest

from mail_settings import MailSettings, load_mail_settings

# Env vars used by load_mail_settings.
_MAIL_ENV_VARS = (
    "SMTP_SERVER",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "EMAIL_FROM",
    "EMAIL_TO",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    """Ensure mail-related env vars are unset before each test."""
    for var in _MAIL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _set_all_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    """Set the minimum env vars required for a successful load."""
    defaults = {
        "SMTP_SERVER": "smtp.example.com",
        "SMTP_PORT": "587",
        "SMTP_USERNAME": "user@example.com",
        "SMTP_PASSWORD": "secret",
        "EMAIL_FROM": "user@example.com",
        "EMAIL_TO": "dest@example.com",
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestLoadMailSettingsFromEnv:
    def test_all_from_env(self, monkeypatch: pytest.MonkeyPatch):
        _set_all_env(monkeypatch)
        ms = load_mail_settings({})
        assert isinstance(ms, MailSettings)
        assert ms.smtp_server == "smtp.example.com"
        assert ms.smtp_port == 587
        assert ms.smtp_username == "user@example.com"
        assert ms.smtp_password == "secret"
        assert ms.from_addr == "user@example.com"
        assert ms.to_addr == "dest@example.com"

    def test_env_overrides_config(self, monkeypatch: pytest.MonkeyPatch):
        _set_all_env(monkeypatch, SMTP_SERVER="env.smtp.com")
        config = {"email": {"smtp_server": "config.smtp.com", "from": "cfg@x.com", "to": "cfg@y.com"}}
        ms = load_mail_settings(config)
        assert ms.smtp_server == "env.smtp.com"


class TestLoadMailSettingsFromConfig:
    def test_fallback_to_config(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        config = {
            "email": {
                "smtp_server": "config.smtp.com",
                "smtp_port": 465,
                "from": "cfg@example.com",
                "to": "dest@example.com",
            }
        }
        ms = load_mail_settings(config)
        assert ms.smtp_server == "config.smtp.com"
        assert ms.smtp_port == 465
        assert ms.smtp_username == "cfg@example.com"  # falls back to email.from
        assert ms.to_addr == "dest@example.com"


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

class TestLoadMailSettingsValidation:
    def test_missing_password_raises(self):
        with pytest.raises(RuntimeError, match="SMTP_PASSWORD"):
            load_mail_settings({"email": {"smtp_server": "s", "from": "a@b", "to": "c@d"}})

    def test_missing_username_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        with pytest.raises(RuntimeError, match="SMTP_USERNAME"):
            load_mail_settings({"email": {"smtp_server": "s", "to": "c@d"}})

    def test_missing_server_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        monkeypatch.setenv("SMTP_USERNAME", "user@x.com")
        with pytest.raises(RuntimeError, match="SMTP_SERVER"):
            load_mail_settings({})

    def test_missing_to_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        monkeypatch.setenv("SMTP_USERNAME", "user@x.com")
        monkeypatch.setenv("SMTP_SERVER", "smtp.x.com")
        monkeypatch.setenv("EMAIL_FROM", "from@x.com")
        with pytest.raises(RuntimeError, match="EMAIL_TO"):
            load_mail_settings({})

    def test_empty_config_dict(self, monkeypatch: pytest.MonkeyPatch):
        """Empty config and no env vars → RuntimeError."""
        with pytest.raises(RuntimeError):
            load_mail_settings({})
