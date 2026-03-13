#!/usr/bin/env python3
"""SQLite-backed runtime state for auth, resets, and manual runs."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
_DB_LOCK = threading.Lock()


def _resolve_db_path() -> Path:
    raw_value = os.getenv("STATE_DB_PATH", "Archive/runtime_state.sqlite3").strip()
    path = Path(raw_value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _connect() -> sqlite3.Connection:
    db_path = _resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30, isolation_level=None, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def initialize_state_store(default_manual_state: dict) -> None:
    payload_json = json.dumps(default_manual_state)
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS login_state (
              client_ip TEXT PRIMARY KEY,
              attempts_json TEXT NOT NULL DEFAULT '[]',
              blocked_until REAL NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reset_tokens (
              token TEXT PRIMARY KEY,
              login_email TEXT NOT NULL,
              expires REAL NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_run_state (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              payload_json TEXT NOT NULL,
              last_manual_speedtest_at REAL NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            "INSERT OR IGNORE INTO metadata(key, value) VALUES ('session_version', '1')"
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO manual_run_state(id, payload_json, last_manual_speedtest_at)
            VALUES (1, ?, 0)
            """,
            (payload_json,),
        )


def _metadata_int(key: str, default: int) -> int:
    with _connect() as connection:
        row = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return default


def get_session_version() -> int:
    return _metadata_int("session_version", 1)


def bump_session_version() -> int:
    with _DB_LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'session_version'"
        ).fetchone()
        try:
            current = int(row["value"]) if row else 1
        except (TypeError, ValueError):
            current = 1
        next_value = current + 1
        connection.execute(
            """
            INSERT INTO metadata(key, value) VALUES ('session_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(next_value),),
        )
    return next_value


def _load_attempts(connection: sqlite3.Connection, client_ip: str) -> tuple[list[float], float]:
    row = connection.execute(
        "SELECT attempts_json, blocked_until FROM login_state WHERE client_ip = ?",
        (client_ip,),
    ).fetchone()
    if not row:
        return [], 0.0
    try:
        attempts = [float(value) for value in json.loads(row["attempts_json"] or "[]")]
    except (TypeError, ValueError, json.JSONDecodeError):
        attempts = []
    blocked_until = float(row["blocked_until"] or 0.0)
    return attempts, blocked_until


def blocked_seconds(client_ip: str, now: float) -> int:
    with _DB_LOCK, _connect() as connection:
        attempts, blocked_until = _load_attempts(connection, client_ip)
        if blocked_until <= now:
            if blocked_until > 0 and not attempts:
                connection.execute("DELETE FROM login_state WHERE client_ip = ?", (client_ip,))
            elif blocked_until > 0:
                connection.execute(
                    "UPDATE login_state SET blocked_until = 0 WHERE client_ip = ?",
                    (client_ip,),
                )
            return 0
        return int(blocked_until - now)


def register_failed_login(
    client_ip: str,
    now: float,
    max_attempts: int,
    window_seconds: int,
    block_seconds: int,
) -> int:
    with _DB_LOCK, _connect() as connection:
        attempts, blocked_until = _load_attempts(connection, client_ip)
        if blocked_until > now:
            return int(blocked_until - now)

        recent = [attempt for attempt in attempts if now - attempt <= window_seconds]
        recent.append(now)
        next_blocked_until = 0.0
        if len(recent) >= max_attempts:
            next_blocked_until = now + block_seconds
            recent = []

        connection.execute(
            """
            INSERT INTO login_state(client_ip, attempts_json, blocked_until)
            VALUES (?, ?, ?)
            ON CONFLICT(client_ip) DO UPDATE SET
              attempts_json = excluded.attempts_json,
              blocked_until = excluded.blocked_until
            """,
            (client_ip, json.dumps(recent), next_blocked_until),
        )
    return block_seconds if next_blocked_until else 0


def clear_login_failures(client_ip: str) -> None:
    with _DB_LOCK, _connect() as connection:
        connection.execute("DELETE FROM login_state WHERE client_ip = ?", (client_ip,))


def cleanup_expired_reset_tokens(now: float) -> None:
    with _DB_LOCK, _connect() as connection:
        connection.execute("DELETE FROM reset_tokens WHERE expires <= ?", (now,))


def store_reset_token(
    token: str,
    login_email: str,
    expires: float,
    now: float | None = None,
    max_pending: int = 10,
) -> None:
    current_time = float(now if now is not None else expires - 900)
    with _DB_LOCK, _connect() as connection:
        connection.execute("DELETE FROM reset_tokens WHERE expires <= ?", (current_time,))
        row = connection.execute("SELECT COUNT(*) AS count FROM reset_tokens WHERE expires > ?", (current_time,)).fetchone()
        pending = int(row["count"] or 0) if row else 0
        if pending >= max_pending:
            raise RuntimeError("Too many pending reset requests")
        connection.execute(
            """
            INSERT INTO reset_tokens(token, login_email, expires)
            VALUES (?, ?, ?)
            """,
            (token, login_email, expires),
        )


def consume_reset_token(token: str, now: float) -> str | None:
    with _DB_LOCK, _connect() as connection:
        connection.execute("DELETE FROM reset_tokens WHERE expires <= ?", (now,))
        row = connection.execute(
            "SELECT login_email, expires FROM reset_tokens WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            return None
        connection.execute("DELETE FROM reset_tokens WHERE token = ?", (token,))
        if float(row["expires"] or 0) <= now:
            return None
        return str(row["login_email"] or "")


def load_manual_runtime_state(default_manual_state: dict) -> tuple[float, dict]:
    payload_json = json.dumps(default_manual_state)
    with _DB_LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT payload_json, last_manual_speedtest_at FROM manual_run_state WHERE id = 1"
        ).fetchone()
        if not row:
            connection.execute(
                """
                INSERT INTO manual_run_state(id, payload_json, last_manual_speedtest_at)
                VALUES (1, ?, 0)
                """,
                (payload_json,),
            )
            return 0.0, dict(default_manual_state)

    try:
        payload = json.loads(row["payload_json"] or payload_json)
    except (TypeError, json.JSONDecodeError):
        payload = dict(default_manual_state)

    return float(row["last_manual_speedtest_at"] or 0.0), payload


def save_manual_runtime_state(payload: dict, last_manual_speedtest_at: float) -> None:
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            """
            INSERT INTO manual_run_state(id, payload_json, last_manual_speedtest_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              payload_json = excluded.payload_json,
              last_manual_speedtest_at = excluded.last_manual_speedtest_at
            """,
            (json.dumps(payload), float(last_manual_speedtest_at)),
        )
