"""Tests for the backup_manager module."""

from __future__ import annotations

import json

import pytest

from backup_manager import (
    _SAFE_FILENAME_RE,
    create_backup,
    delete_backup,
    get_backup_path,
    list_backups,
    restore_backup,
    save_backup_to_path,
    validate_backup,
)
from measurement_store import get_app_secret, set_app_secret
from state_store import (
    bump_session_version,
    consume_reset_token,
    get_notification_log,
    get_session_version,
    initialize_state_store,
    load_manual_runtime_state,
    load_speedtest_completion_state,
    log_notification,
    record_speedtest_completion,
    store_reset_token,
)

DEFAULT_MANUAL_STATE = {
    "status": "idle",
    "stage": "Idle",
    "message": "",
    "logs": [],
    "selected_server_id": "",
    "selected_server_label": "Auto (nearest server)",
    "started_at": None,
    "completed_at": None,
    "updated_at": None,
    "exit_code": None,
}


@pytest.fixture()
def backup_env(monkeypatch, tmp_path):
    """Set up an isolated environment for backup tests."""
    import backup_manager

    monkeypatch.setattr(backup_manager, "SCRIPT_DIR", tmp_path)

    # Create config.json
    config = {
        "account": {"name": "Test User", "provider": "TestISP"},
        "paths": {"log_directory": "Log"},
        "thresholds": {"download_mbps": 500},
        "backup": {"backup_directory": "Backups", "max_backups": 3},
    }
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")

    # Create .env with mixed keys
    env_lines = [
        'TZ="UTC"',
        'APP_SECRET_KEY="super-secret-key-should-not-be-backed-up"',
        'AUTH_SALT="deadbeef"',
        'DASHBOARD_LOGIN_EMAIL="test@example.com"',
        'DASHBOARD_PASSWORD_HASH="pbkdf2_sha256:390000:aabb:ccdd"',
        'SMTP_PASSWORD="mail-pass"',
    ]
    (tmp_path / ".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    # Create SQLite DB (just a dummy file for testing)
    db_dir = tmp_path / "Archive"
    db_dir.mkdir()
    db_path = db_dir / "runtime_state.sqlite3"
    db_path.write_bytes(b"SQLITE_FAKE_DB_CONTENT")
    monkeypatch.setenv("STATE_DB_PATH", str(db_path))

    # Create speed log files
    log_dir = tmp_path / "Log"
    log_dir.mkdir()
    (log_dir / "speed_log_week_01.txt").write_text("log data week 1\n", encoding="utf-8")
    (log_dir / "speed_log_week_02.txt").write_text("log data week 2\n", encoding="utf-8")

    # Create Backups dir
    (tmp_path / "Backups").mkdir()

    initialize_state_store(DEFAULT_MANUAL_STATE)
    bump_session_version()
    store_reset_token("backup-token", "backup@example.com", expires=9999999999.0, now=10.0)
    record_speedtest_completion("success", "manual", completed_at=456.0)
    log_notification("email", "health_check", "Backup fixture notification")
    set_app_secret("smtp_password", "db-mail-pass")

    return tmp_path


class TestCreateBackup:
    def test_creates_encrypted_backup(self, backup_env):
        encrypted, filename = create_backup("testpass123", include_logs=True)
        assert filename.startswith("speedpulse_")
        assert filename.endswith(".speedpulse-backup")
        assert len(encrypted) > 100

    def test_password_too_short_raises(self, backup_env):
        with pytest.raises(ValueError, match="at least 6"):
            create_backup("short")

    def test_empty_password_raises(self, backup_env):
        with pytest.raises(ValueError, match="at least 6"):
            create_backup("")

    def test_without_logs(self, backup_env):
        encrypted_with_logs, _ = create_backup("testpass123", include_logs=True)
        encrypted_without_logs, _ = create_backup("testpass123", include_logs=False)
        # Without logs should be smaller
        assert len(encrypted_without_logs) < len(encrypted_with_logs)


class TestValidateBackup:
    def test_valid_backup(self, backup_env):
        encrypted, _ = create_backup("testpass123")
        manifest = validate_backup(encrypted, "testpass123")
        assert "version" in manifest
        assert "created_at" in manifest
        assert "files" in manifest
        assert "config.json" in manifest["files"]
        assert "runtime_state.json" in manifest["files"]

    def test_wrong_password(self, backup_env):
        encrypted, _ = create_backup("testpass123")
        with pytest.raises(ValueError, match="Wrong backup password"):
            validate_backup(encrypted, "wrongpassword")

    def test_corrupted_data(self, backup_env):
        with pytest.raises(ValueError):
            validate_backup(b"not-a-real-backup-file-at-all!!", "testpass123")


class TestRestoreBackup:
    def test_round_trip(self, backup_env):
        """Create a backup, modify files, then restore and verify."""
        password = "testpass123"
        encrypted, _ = create_backup(password, include_logs=True)

        # Modify current data
        new_config = {"account": {"name": "Modified"}, "thresholds": {"download_mbps": 999}}
        (backup_env / "config.json").write_text(json.dumps(new_config), encoding="utf-8")
        (backup_env / "Log" / "speed_log_week_01.txt").write_text("MODIFIED", encoding="utf-8")
        set_app_secret("smtp_password", "changed-db-mail-pass")

        # Restore
        summary = restore_backup(encrypted, password)
        assert "config.json" in summary["restored"]
        assert ".env settings" in summary["restored"]
        assert "runtime_state.sqlite3" in summary["restored"]
        assert "runtime_state.json" in summary["restored"]
        assert any("speed log" in item for item in summary["restored"])

        # Verify config.json was restored
        restored_config = json.loads((backup_env / "config.json").read_text(encoding="utf-8"))
        assert restored_config["account"]["name"] == "Test User"

        # Verify log was restored
        assert (backup_env / "Log" / "speed_log_week_01.txt").read_text(encoding="utf-8") == "log data week 1\n"
        assert get_app_secret("smtp_password") == "db-mail-pass"

        last_run_at, payload = load_manual_runtime_state(DEFAULT_MANUAL_STATE)
        assert last_run_at == 0.0
        assert payload["status"] == "idle"

        completion = load_speedtest_completion_state()
        assert completion["sequence"] == 1
        assert completion["status"] == "success"
        assert completion["source"] == "manual"
        assert get_session_version() == 2
        assert consume_reset_token("backup-token", now=100.0) == "backup@example.com"
        entries = get_notification_log(limit=10)
        assert any(entry["summary"] == "Backup fixture notification" for entry in entries)

    def test_env_security_tokens_preserved(self, backup_env):
        """APP_SECRET_KEY and AUTH_SALT should NOT be overwritten."""
        password = "testpass123"
        encrypted, _ = create_backup(password)

        # Change the security tokens in .env
        env_content = (backup_env / ".env").read_text(encoding="utf-8")
        env_content = env_content.replace("super-secret-key-should-not-be-backed-up", "NEW-SECRET-KEY")
        env_content = env_content.replace("deadbeef", "newbeef1234")
        (backup_env / ".env").write_text(env_content, encoding="utf-8")

        # Restore
        restore_backup(encrypted, password)

        # Security tokens should still be the NEW values (not overwritten from backup)
        env_after = (backup_env / ".env").read_text(encoding="utf-8")
        assert "NEW-SECRET-KEY" in env_after
        assert "newbeef1234" in env_after

    def test_wrong_password_fails(self, backup_env):
        encrypted, _ = create_backup("testpass123")
        with pytest.raises(ValueError, match="Wrong backup password"):
            restore_backup(encrypted, "badpassword")


class TestBackupDirectory:
    def test_save_and_list(self, backup_env):
        config = json.loads((backup_env / "config.json").read_text(encoding="utf-8"))
        encrypted, filename = create_backup("testpass123")
        save_backup_to_path(encrypted, filename, config)

        backups = list_backups(config)
        assert len(backups) == 1
        assert backups[0]["filename"] == filename
        assert backups[0]["size_bytes"] > 0

    def test_max_backups_enforced(self, backup_env):
        config = json.loads((backup_env / "config.json").read_text(encoding="utf-8"))
        config["backup"]["max_backups"] = 2

        filenames = []
        for i in range(4):
            encrypted, filename = create_backup("testpass123")
            # Use unique filenames
            unique_name = f"speedpulse_test_{i}.speedpulse-backup"
            save_backup_to_path(encrypted, unique_name, config)
            filenames.append(unique_name)

        backups = list_backups(config)
        assert len(backups) == 2

    def test_delete_backup(self, backup_env):
        config = json.loads((backup_env / "config.json").read_text(encoding="utf-8"))
        encrypted, filename = create_backup("testpass123")
        save_backup_to_path(encrypted, filename, config)

        assert delete_backup(filename, config) is True
        assert list_backups(config) == []

    def test_delete_nonexistent(self, backup_env):
        config = json.loads((backup_env / "config.json").read_text(encoding="utf-8"))
        assert delete_backup("nonexistent.speedpulse-backup", config) is False

    def test_get_backup_path_traversal_blocked(self, backup_env):
        config = json.loads((backup_env / "config.json").read_text(encoding="utf-8"))
        assert get_backup_path("../../../etc/passwd", config) is None
        assert get_backup_path("../../config.json", config) is None
        assert get_backup_path("foo/bar.speedpulse-backup", config) is None

    def test_filename_validation(self):
        assert _SAFE_FILENAME_RE.match("speedpulse_2026-03-15_143022.speedpulse-backup")
        assert _SAFE_FILENAME_RE.match("my-backup.speedpulse-backup")
        assert not _SAFE_FILENAME_RE.match("../evil.speedpulse-backup")
        assert not _SAFE_FILENAME_RE.match("backup.zip")
        assert not _SAFE_FILENAME_RE.match("backup with spaces.speedpulse-backup")
        assert not _SAFE_FILENAME_RE.match("")
