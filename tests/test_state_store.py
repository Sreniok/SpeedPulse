"""Tests for runtime/auth state backed by the shared SQL database."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from state_store import (
    blocked_seconds,
    bump_session_version,
    clear_login_failures,
    consume_reset_token,
    get_notification_log,
    get_session_version,
    initialize_state_store,
    load_manual_runtime_state,
    load_speedtest_completion_state,
    log_notification,
    record_speedtest_completion,
    register_failed_login,
    save_manual_runtime_state,
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


def test_runtime_state_round_trip_uses_shared_sql_backend():
    initialize_state_store(DEFAULT_MANUAL_STATE)

    assert get_session_version() == 1
    assert bump_session_version() == 2
    assert get_session_version() == 2

    save_manual_runtime_state({"status": "running", "logs": ["hello"]}, 123.0)
    last_run_at, payload = load_manual_runtime_state(DEFAULT_MANUAL_STATE)
    assert last_run_at == 123.0
    assert payload["status"] == "running"
    assert payload["logs"] == ["hello"]

    store_reset_token("token-1", "user@example.com", expires=9999999999.0, now=10.0)
    assert consume_reset_token("token-1", now=20.0) == "user@example.com"
    assert consume_reset_token("token-1", now=20.0) is None

    record_speedtest_completion("success", "manual", completed_at=111.0)
    completion = load_speedtest_completion_state()
    assert completion["sequence"] == 1
    assert completion["status"] == "success"
    assert completion["source"] == "manual"
    assert completion["completed_at"] == 111.0

    log_notification("email", "weekly_report", "Report sent")
    entries = get_notification_log(limit=10)
    assert len(entries) == 1
    assert entries[0]["event_type"] == "weekly_report"


def test_login_blocking_state_round_trip():
    initialize_state_store(DEFAULT_MANUAL_STATE)

    assert blocked_seconds("203.0.113.10", now=100.0) == 0
    assert register_failed_login("203.0.113.10", 100.0, 3, 300, 900) == 0
    assert register_failed_login("203.0.113.10", 150.0, 3, 300, 900) == 0
    assert register_failed_login("203.0.113.10", 200.0, 3, 300, 900) == 900
    assert blocked_seconds("203.0.113.10", now=250.0) == 850

    clear_login_failures("203.0.113.10")
    assert blocked_seconds("203.0.113.10", now=250.0) == 0


def test_initialize_imports_legacy_sqlite_runtime_state(monkeypatch, tmp_path: Path):
    legacy_db = tmp_path / "runtime_state.sqlite3"
    monkeypatch.setenv("STATE_DB_PATH", str(legacy_db))

    with sqlite3.connect(legacy_db) as connection:
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES ('session_version', '7')"
        )
        connection.execute(
            """
            CREATE TABLE login_state (
              client_ip TEXT PRIMARY KEY,
              attempts_json TEXT NOT NULL DEFAULT '[]',
              blocked_until REAL NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            """
            INSERT INTO login_state(client_ip, attempts_json, blocked_until)
            VALUES ('198.51.100.10', '[10.0, 20.0]', 999.0)
            """
        )
        connection.execute(
            """
            CREATE TABLE reset_tokens (
              token TEXT PRIMARY KEY,
              login_email TEXT NOT NULL,
              expires REAL NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO reset_tokens(token, login_email, expires)
            VALUES ('legacy-token', 'legacy@example.com', 9999999999.0)
            """
        )
        connection.execute(
            """
            CREATE TABLE manual_run_state (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              payload_json TEXT NOT NULL,
              last_manual_speedtest_at REAL NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            """
            INSERT INTO manual_run_state(id, payload_json, last_manual_speedtest_at)
            VALUES (1, '{"status":"failed","logs":["legacy"]}', 555.0)
            """
        )
        connection.execute(
            """
            CREATE TABLE speedtest_completion_state (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              sequence INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'unknown',
              source TEXT NOT NULL DEFAULT 'unknown',
              completed_at REAL NOT NULL DEFAULT 0,
              updated_at REAL NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            """
            INSERT INTO speedtest_completion_state(
              id, sequence, status, source, completed_at, updated_at
            ) VALUES (1, 3, 'failed', 'scheduled', 777.0, 778.0)
            """
        )
        connection.execute(
            """
            CREATE TABLE notification_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              timestamp REAL NOT NULL,
              channel TEXT NOT NULL,
              event_type TEXT NOT NULL,
              summary TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(
            """
            INSERT INTO notification_log(timestamp, channel, event_type, summary)
            VALUES (1234.0, 'email', 'health_check', 'Legacy alert')
            """
        )

    initialize_state_store(DEFAULT_MANUAL_STATE)

    assert get_session_version() == 7
    assert blocked_seconds("198.51.100.10", now=100.0) == 899
    assert consume_reset_token("legacy-token", now=100.0) == "legacy@example.com"

    last_run_at, payload = load_manual_runtime_state(DEFAULT_MANUAL_STATE)
    assert last_run_at == 555.0
    assert payload["status"] == "failed"
    assert payload["logs"] == ["legacy"]

    completion = load_speedtest_completion_state()
    assert completion["sequence"] == 3
    assert completion["status"] == "failed"
    assert completion["source"] == "scheduled"

    entries = get_notification_log(limit=10)
    assert len(entries) == 1
    assert entries[0]["event_type"] == "health_check"
    assert entries[0]["summary"] == "Legacy alert"
