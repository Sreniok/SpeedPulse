#!/usr/bin/env python3
"""Runtime state for auth, resets, manual runs, and notification history.

Uses the shared SQL database when ``DATABASE_URL`` is configured and falls back
to the legacy local SQLite file otherwise.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import insert, select

from measurement_store import (
    NOTIFICATION_EVENTS,
    RUNTIME_LOGIN_STATE,
    RUNTIME_MANUAL_RUN_STATE,
    RUNTIME_METADATA,
    RUNTIME_RESET_TOKENS,
    RUNTIME_SPEEDTEST_COMPLETION_STATE,
    database_enabled,
    get_engine,
    list_notification_events,
    run_migrations,
)

PROJECT_ROOT = Path(__file__).resolve().parent
_DB_LOCK = threading.Lock()


def _resolve_db_path() -> Path:
    raw_value = os.getenv("STATE_DB_PATH", "Archive/runtime_state.sqlite3").strip()
    data_root = os.getenv("APP_DATA_DIR", "").strip()
    path = Path(raw_value)
    if path.is_absolute():
        return path
    if data_root:
        root = Path(data_root)
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        return root / path
    return PROJECT_ROOT / path


def _use_shared_database() -> bool:
    return database_enabled()


def _connect_sqlite() -> sqlite3.Connection:
    db_path = _resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30, isolation_level=None, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def get_state_db_path() -> Path:
    """Return the effective legacy SQLite path used for fallback/import."""
    return _resolve_db_path()


def _sqlite_initialize_state_store(default_manual_state: dict) -> None:
    payload_json = json.dumps(default_manual_state)
    with _DB_LOCK, _connect_sqlite() as connection:
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
            """
            CREATE TABLE IF NOT EXISTS speedtest_completion_state (
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
            "INSERT OR IGNORE INTO metadata(key, value) VALUES ('session_version', '1')"
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO manual_run_state(id, payload_json, last_manual_speedtest_at)
            VALUES (1, ?, 0)
            """,
            (payload_json,),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO speedtest_completion_state(
              id, sequence, status, source, completed_at, updated_at
            ) VALUES (1, 0, 'unknown', 'unknown', 0, 0)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              timestamp REAL NOT NULL,
              channel TEXT NOT NULL,
              event_type TEXT NOT NULL,
              summary TEXT NOT NULL DEFAULT ''
            )
            """
        )


def _postgres_metadata_value(connection, key: str, default: str = "") -> str:
    row = connection.execute(
        select(RUNTIME_METADATA.c.value).where(RUNTIME_METADATA.c.key == key)
    ).mappings().first()
    if not row:
        return default
    return str(row["value"] or default)


def _postgres_set_metadata(connection, key: str, value: str) -> None:
    existing = connection.execute(
        select(RUNTIME_METADATA.c.key).where(RUNTIME_METADATA.c.key == key)
    ).first()
    if existing:
        connection.execute(
            RUNTIME_METADATA.update()
            .where(RUNTIME_METADATA.c.key == key)
            .values(value=str(value))
        )
    else:
        connection.execute(insert(RUNTIME_METADATA).values(key=key, value=str(value)))


def _postgres_runtime_state_empty(connection) -> bool:
    has_login = connection.execute(select(RUNTIME_LOGIN_STATE.c.client_ip).limit(1)).first()
    has_reset = connection.execute(select(RUNTIME_RESET_TOKENS.c.token).limit(1)).first()
    has_notifications = connection.execute(select(NOTIFICATION_EVENTS.c.id).limit(1)).first()
    manual_row = connection.execute(
        select(
            RUNTIME_MANUAL_RUN_STATE.c.last_manual_speedtest_at,
            RUNTIME_MANUAL_RUN_STATE.c.payload_json,
        ).where(RUNTIME_MANUAL_RUN_STATE.c.id == 1)
    ).mappings().first()
    completion_row = connection.execute(
        select(RUNTIME_SPEEDTEST_COMPLETION_STATE.c.sequence).where(
            RUNTIME_SPEEDTEST_COMPLETION_STATE.c.id == 1
        )
    ).mappings().first()
    session_version = _postgres_metadata_value(connection, "session_version", "1")
    manual_last_run = float((manual_row or {}).get("last_manual_speedtest_at") or 0.0)
    completion_sequence = int((completion_row or {}).get("sequence") or 0)
    return not any(
        [
            has_login,
            has_reset,
            has_notifications,
            manual_last_run > 0,
            completion_sequence > 0,
            session_version not in {"", "1"},
        ]
    )


def _import_legacy_sqlite_state(default_manual_state: dict) -> None:
    legacy_path = _resolve_db_path()
    if not legacy_path.is_file():
        return

    try:
        with sqlite3.connect(legacy_path) as legacy_connection:
            legacy_connection.row_factory = sqlite3.Row
            table_names = {
                str(row["name"])
                for row in legacy_connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if not table_names:
                return

            session_version = 1
            if "metadata" in table_names:
                row = legacy_connection.execute(
                    "SELECT value FROM metadata WHERE key = 'session_version'"
                ).fetchone()
                try:
                    session_version = int(row["value"]) if row else 1
                except (TypeError, ValueError):
                    session_version = 1

            login_rows = []
            if "login_state" in table_names:
                login_rows = legacy_connection.execute(
                    "SELECT client_ip, attempts_json, blocked_until FROM login_state"
                ).fetchall()

            reset_rows = []
            if "reset_tokens" in table_names:
                reset_rows = legacy_connection.execute(
                    "SELECT token, login_email, expires FROM reset_tokens"
                ).fetchall()

            manual_row = None
            if "manual_run_state" in table_names:
                manual_row = legacy_connection.execute(
                    "SELECT payload_json, last_manual_speedtest_at FROM manual_run_state WHERE id = 1"
                ).fetchone()

            completion_row = None
            if "speedtest_completion_state" in table_names:
                completion_row = legacy_connection.execute(
                    """
                    SELECT sequence, status, source, completed_at, updated_at
                    FROM speedtest_completion_state
                    WHERE id = 1
                    """
                ).fetchone()

            notification_rows = []
            if "notification_log" in table_names:
                notification_rows = legacy_connection.execute(
                    "SELECT timestamp, channel, event_type, summary FROM notification_log ORDER BY id ASC"
                ).fetchall()
    except sqlite3.DatabaseError:
        return

    with get_engine().begin() as connection:
        if _postgres_metadata_value(connection, "legacy_sqlite_imported", "") == "1":
            return
        if not _postgres_runtime_state_empty(connection):
            _postgres_set_metadata(connection, "legacy_sqlite_imported", "1")
            return

        _postgres_set_metadata(connection, "session_version", str(max(1, session_version)))

        for row in login_rows:
            connection.execute(
                insert(RUNTIME_LOGIN_STATE).values(
                    client_ip=str(row["client_ip"] or ""),
                    attempts_json=str(row["attempts_json"] or "[]"),
                    blocked_until=float(row["blocked_until"] or 0.0),
                )
            )

        current_time = time.time()
        for row in reset_rows:
            expires = float(row["expires"] or 0.0)
            if expires <= current_time:
                continue
            connection.execute(
                insert(RUNTIME_RESET_TOKENS).values(
                    token=str(row["token"] or ""),
                    login_email=str(row["login_email"] or ""),
                    expires=expires,
                )
            )

        payload_json = json.dumps(default_manual_state)
        last_manual_speedtest_at = 0.0
        if manual_row:
            payload_json = str(manual_row["payload_json"] or payload_json)
            last_manual_speedtest_at = float(manual_row["last_manual_speedtest_at"] or 0.0)
        connection.execute(
            RUNTIME_MANUAL_RUN_STATE.update()
            .where(RUNTIME_MANUAL_RUN_STATE.c.id == 1)
            .values(
                payload_json=payload_json,
                last_manual_speedtest_at=last_manual_speedtest_at,
            )
        )

        if completion_row:
            connection.execute(
                RUNTIME_SPEEDTEST_COMPLETION_STATE.update()
                .where(RUNTIME_SPEEDTEST_COMPLETION_STATE.c.id == 1)
                .values(
                    sequence=int(completion_row["sequence"] or 0),
                    status=str(completion_row["status"] or "unknown"),
                    source=str(completion_row["source"] or "unknown"),
                    completed_at=float(completion_row["completed_at"] or 0.0),
                    updated_at=float(completion_row["updated_at"] or 0.0),
                )
            )

        for row in notification_rows:
            created_at = datetime.fromtimestamp(float(row["timestamp"] or current_time), tz=timezone.utc)
            connection.execute(
                insert(NOTIFICATION_EVENTS).values(
                    created_at=created_at,
                    channel=str(row["channel"] or "unknown"),
                    event_type=str(row["event_type"] or "unknown"),
                    summary=str(row["summary"] or ""),
                )
            )

        _postgres_set_metadata(connection, "legacy_sqlite_imported", "1")


def _postgres_initialize_state_store(default_manual_state: dict) -> None:
    payload_json = json.dumps(default_manual_state)
    run_migrations()
    with _DB_LOCK, get_engine().begin() as connection:
        if not connection.execute(
            select(RUNTIME_METADATA.c.key).where(RUNTIME_METADATA.c.key == "session_version")
        ).first():
            connection.execute(insert(RUNTIME_METADATA).values(key="session_version", value="1"))
        if not connection.execute(
            select(RUNTIME_MANUAL_RUN_STATE.c.id).where(RUNTIME_MANUAL_RUN_STATE.c.id == 1)
        ).first():
            connection.execute(
                insert(RUNTIME_MANUAL_RUN_STATE).values(
                    id=1,
                    payload_json=payload_json,
                    last_manual_speedtest_at=0.0,
                )
            )
        if not connection.execute(
            select(RUNTIME_SPEEDTEST_COMPLETION_STATE.c.id).where(
                RUNTIME_SPEEDTEST_COMPLETION_STATE.c.id == 1
            )
        ).first():
            connection.execute(
                insert(RUNTIME_SPEEDTEST_COMPLETION_STATE).values(
                    id=1,
                    sequence=0,
                    status="unknown",
                    source="unknown",
                    completed_at=0.0,
                    updated_at=0.0,
                )
            )
    _import_legacy_sqlite_state(default_manual_state)


def initialize_state_store(default_manual_state: dict) -> None:
    if _use_shared_database():
        _postgres_initialize_state_store(default_manual_state)
        return
    _sqlite_initialize_state_store(default_manual_state)


def _sqlite_metadata_int(key: str, default: int) -> int:
    with _connect_sqlite() as connection:
        row = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return default


def _postgres_metadata_int(key: str, default: int) -> int:
    run_migrations()
    with get_engine().connect() as connection:
        raw_value = _postgres_metadata_value(connection, key, str(default))
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return default


def get_session_version() -> int:
    if _use_shared_database():
        return _postgres_metadata_int("session_version", 1)
    return _sqlite_metadata_int("session_version", 1)


def bump_session_version() -> int:
    if _use_shared_database():
        run_migrations()
        with _DB_LOCK, get_engine().begin() as connection:
            current = _postgres_metadata_int("session_version", 1)
            next_value = current + 1
            _postgres_set_metadata(connection, "session_version", str(next_value))
        return next_value

    with _DB_LOCK, _connect_sqlite() as connection:
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


def _sqlite_load_attempts(connection: sqlite3.Connection, client_ip: str) -> tuple[list[float], float]:
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


def _postgres_load_attempts(connection, client_ip: str) -> tuple[list[float], float]:
    row = connection.execute(
        select(RUNTIME_LOGIN_STATE.c.attempts_json, RUNTIME_LOGIN_STATE.c.blocked_until).where(
            RUNTIME_LOGIN_STATE.c.client_ip == client_ip
        )
    ).mappings().first()
    if not row:
        return [], 0.0
    try:
        attempts = [float(value) for value in json.loads(row["attempts_json"] or "[]")]
    except (TypeError, ValueError, json.JSONDecodeError):
        attempts = []
    return attempts, float(row["blocked_until"] or 0.0)


def blocked_seconds(client_ip: str, now: float) -> int:
    if _use_shared_database():
        run_migrations()
        with _DB_LOCK, get_engine().begin() as connection:
            attempts, blocked_until = _postgres_load_attempts(connection, client_ip)
            if blocked_until <= now:
                if blocked_until > 0 and not attempts:
                    connection.execute(
                        RUNTIME_LOGIN_STATE.delete().where(RUNTIME_LOGIN_STATE.c.client_ip == client_ip)
                    )
                elif blocked_until > 0:
                    connection.execute(
                        RUNTIME_LOGIN_STATE.update()
                        .where(RUNTIME_LOGIN_STATE.c.client_ip == client_ip)
                        .values(blocked_until=0.0)
                    )
                return 0
            return int(blocked_until - now)

    with _DB_LOCK, _connect_sqlite() as connection:
        attempts, blocked_until = _sqlite_load_attempts(connection, client_ip)
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
    if _use_shared_database():
        run_migrations()
        with _DB_LOCK, get_engine().begin() as connection:
            attempts, blocked_until = _postgres_load_attempts(connection, client_ip)
            if blocked_until > now:
                return int(blocked_until - now)

            recent = [attempt for attempt in attempts if now - attempt <= window_seconds]
            recent.append(now)
            next_blocked_until = 0.0
            if len(recent) >= max_attempts:
                next_blocked_until = now + block_seconds
                recent = []

            existing = connection.execute(
                select(RUNTIME_LOGIN_STATE.c.client_ip).where(
                    RUNTIME_LOGIN_STATE.c.client_ip == client_ip
                )
            ).first()
            payload = {
                "client_ip": client_ip,
                "attempts_json": json.dumps(recent),
                "blocked_until": next_blocked_until,
            }
            if existing:
                connection.execute(
                    RUNTIME_LOGIN_STATE.update()
                    .where(RUNTIME_LOGIN_STATE.c.client_ip == client_ip)
                    .values(**payload)
                )
            else:
                connection.execute(insert(RUNTIME_LOGIN_STATE).values(**payload))
        return block_seconds if next_blocked_until else 0

    with _DB_LOCK, _connect_sqlite() as connection:
        attempts, blocked_until = _sqlite_load_attempts(connection, client_ip)
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
    if _use_shared_database():
        run_migrations()
        with _DB_LOCK, get_engine().begin() as connection:
            connection.execute(
                RUNTIME_LOGIN_STATE.delete().where(RUNTIME_LOGIN_STATE.c.client_ip == client_ip)
            )
        return

    with _DB_LOCK, _connect_sqlite() as connection:
        connection.execute("DELETE FROM login_state WHERE client_ip = ?", (client_ip,))


def cleanup_expired_reset_tokens(now: float) -> None:
    if _use_shared_database():
        run_migrations()
        with _DB_LOCK, get_engine().begin() as connection:
            connection.execute(
                RUNTIME_RESET_TOKENS.delete().where(RUNTIME_RESET_TOKENS.c.expires <= now)
            )
        return

    with _DB_LOCK, _connect_sqlite() as connection:
        connection.execute("DELETE FROM reset_tokens WHERE expires <= ?", (now,))


def store_reset_token(
    token: str,
    login_email: str,
    expires: float,
    now: float | None = None,
    max_pending: int = 10,
) -> None:
    current_time = float(now if now is not None else expires - 900)
    if _use_shared_database():
        run_migrations()
        with _DB_LOCK, get_engine().begin() as connection:
            connection.execute(
                RUNTIME_RESET_TOKENS.delete().where(RUNTIME_RESET_TOKENS.c.expires <= current_time)
            )
            pending_rows = connection.execute(
                select(RUNTIME_RESET_TOKENS.c.token).where(RUNTIME_RESET_TOKENS.c.expires > current_time)
            ).fetchall()
            if len(pending_rows) >= max_pending:
                raise RuntimeError("Too many pending reset requests")
            connection.execute(
                insert(RUNTIME_RESET_TOKENS).values(
                    token=token,
                    login_email=login_email,
                    expires=expires,
                )
            )
        return

    with _DB_LOCK, _connect_sqlite() as connection:
        connection.execute("DELETE FROM reset_tokens WHERE expires <= ?", (current_time,))
        pending_rows = connection.execute(
            "SELECT token FROM reset_tokens WHERE expires > ?",
            (current_time,),
        ).fetchall()
        pending = len(pending_rows)
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
    if _use_shared_database():
        run_migrations()
        with _DB_LOCK, get_engine().begin() as connection:
            connection.execute(
                RUNTIME_RESET_TOKENS.delete().where(RUNTIME_RESET_TOKENS.c.expires <= now)
            )
            row = connection.execute(
                select(RUNTIME_RESET_TOKENS.c.login_email, RUNTIME_RESET_TOKENS.c.expires).where(
                    RUNTIME_RESET_TOKENS.c.token == token
                )
            ).mappings().first()
            if not row:
                return None
            connection.execute(
                RUNTIME_RESET_TOKENS.delete().where(RUNTIME_RESET_TOKENS.c.token == token)
            )
            if float(row["expires"] or 0.0) <= now:
                return None
            return str(row["login_email"] or "")

    with _DB_LOCK, _connect_sqlite() as connection:
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
    if _use_shared_database():
        run_migrations()
        with _DB_LOCK, get_engine().begin() as connection:
            row = connection.execute(
                select(
                    RUNTIME_MANUAL_RUN_STATE.c.payload_json,
                    RUNTIME_MANUAL_RUN_STATE.c.last_manual_speedtest_at,
                ).where(RUNTIME_MANUAL_RUN_STATE.c.id == 1)
            ).mappings().first()
            if not row:
                connection.execute(
                    insert(RUNTIME_MANUAL_RUN_STATE).values(
                        id=1,
                        payload_json=payload_json,
                        last_manual_speedtest_at=0.0,
                    )
                )
                return 0.0, dict(default_manual_state)

        try:
            payload = json.loads(str(row["payload_json"] or payload_json))
        except (TypeError, json.JSONDecodeError):
            payload = dict(default_manual_state)
        return float(row["last_manual_speedtest_at"] or 0.0), payload

    with _DB_LOCK, _connect_sqlite() as connection:
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
    if _use_shared_database():
        run_migrations()
        with _DB_LOCK, get_engine().begin() as connection:
            existing = connection.execute(
                select(RUNTIME_MANUAL_RUN_STATE.c.id).where(RUNTIME_MANUAL_RUN_STATE.c.id == 1)
            ).first()
            values = {
                "payload_json": json.dumps(payload),
                "last_manual_speedtest_at": float(last_manual_speedtest_at),
            }
            if existing:
                connection.execute(
                    RUNTIME_MANUAL_RUN_STATE.update()
                    .where(RUNTIME_MANUAL_RUN_STATE.c.id == 1)
                    .values(**values)
                )
            else:
                connection.execute(insert(RUNTIME_MANUAL_RUN_STATE).values(id=1, **values))
        return

    with _DB_LOCK, _connect_sqlite() as connection:
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


def load_speedtest_completion_state() -> dict:
    if _use_shared_database():
        run_migrations()
        with get_engine().begin() as connection:
            row = connection.execute(
                select(
                    RUNTIME_SPEEDTEST_COMPLETION_STATE.c.sequence,
                    RUNTIME_SPEEDTEST_COMPLETION_STATE.c.status,
                    RUNTIME_SPEEDTEST_COMPLETION_STATE.c.source,
                    RUNTIME_SPEEDTEST_COMPLETION_STATE.c.completed_at,
                    RUNTIME_SPEEDTEST_COMPLETION_STATE.c.updated_at,
                ).where(RUNTIME_SPEEDTEST_COMPLETION_STATE.c.id == 1)
            ).mappings().first()
            if not row:
                connection.execute(
                    insert(RUNTIME_SPEEDTEST_COMPLETION_STATE).values(
                        id=1,
                        sequence=0,
                        status="unknown",
                        source="unknown",
                        completed_at=0.0,
                        updated_at=0.0,
                    )
                )
                values = {
                    "sequence": 0,
                    "status": "unknown",
                    "source": "unknown",
                    "completed_at": 0.0,
                    "updated_at": 0.0,
                }
            else:
                values = dict(row)
        return {
            "sequence": int(str(values["sequence"] or 0)),
            "status": str(values["status"] or "unknown"),
            "source": str(values["source"] or "unknown"),
            "completed_at": float(str(values["completed_at"] or 0.0)),
            "updated_at": float(str(values["updated_at"] or 0.0)),
        }

    with _connect_sqlite() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS speedtest_completion_state (
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
            INSERT OR IGNORE INTO speedtest_completion_state(
              id, sequence, status, source, completed_at, updated_at
            ) VALUES (1, 0, 'unknown', 'unknown', 0, 0)
            """
        )
        row = connection.execute(
            "SELECT sequence, status, source, completed_at, updated_at FROM speedtest_completion_state WHERE id = 1"
        ).fetchone()

    if not row:
        return {
            "sequence": 0,
            "status": "unknown",
            "source": "unknown",
            "completed_at": 0.0,
            "updated_at": 0.0,
        }

    return {
        "sequence": int(row["sequence"] or 0),
        "status": str(row["status"] or "unknown"),
        "source": str(row["source"] or "unknown"),
        "completed_at": float(row["completed_at"] or 0.0),
        "updated_at": float(row["updated_at"] or 0.0),
    }


def record_speedtest_completion(status: str, source: str, completed_at: float | None = None) -> dict:
    normalized_status = str(status or "failed").strip().lower()
    if normalized_status not in {"success", "failed"}:
        normalized_status = "failed"

    normalized_source = str(source or "unknown").strip().lower()
    if normalized_source not in {"manual", "scheduled"}:
        normalized_source = "unknown"

    completed_ts = float(completed_at if completed_at is not None else time.time())
    updated_ts = time.time()

    if _use_shared_database():
        run_migrations()
        with _DB_LOCK, get_engine().begin() as connection:
            row = connection.execute(
                select(RUNTIME_SPEEDTEST_COMPLETION_STATE.c.sequence).where(
                    RUNTIME_SPEEDTEST_COMPLETION_STATE.c.id == 1
                )
            ).mappings().first()
            current_sequence = int(row["sequence"] or 0) if row else 0
            sequence = current_sequence + 1
            existing = connection.execute(
                select(RUNTIME_SPEEDTEST_COMPLETION_STATE.c.id).where(
                    RUNTIME_SPEEDTEST_COMPLETION_STATE.c.id == 1
                )
            ).first()
            values = {
                "sequence": sequence,
                "status": normalized_status,
                "source": normalized_source,
                "completed_at": completed_ts,
                "updated_at": updated_ts,
            }
            if existing:
                connection.execute(
                    RUNTIME_SPEEDTEST_COMPLETION_STATE.update()
                    .where(RUNTIME_SPEEDTEST_COMPLETION_STATE.c.id == 1)
                    .values(**values)
                )
            else:
                connection.execute(
                    insert(RUNTIME_SPEEDTEST_COMPLETION_STATE).values(id=1, **values)
                )
        return {
            "sequence": sequence,
            "status": normalized_status,
            "source": normalized_source,
            "completed_at": completed_ts,
            "updated_at": updated_ts,
        }

    with _DB_LOCK, _connect_sqlite() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS speedtest_completion_state (
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
            INSERT OR IGNORE INTO speedtest_completion_state(
              id, sequence, status, source, completed_at, updated_at
            ) VALUES (1, 0, 'unknown', 'unknown', 0, 0)
            """
        )
        row = connection.execute(
            "SELECT sequence FROM speedtest_completion_state WHERE id = 1"
        ).fetchone()
        sequence = (int(row["sequence"] or 0) if row else 0) + 1
        connection.execute(
            """
            INSERT INTO speedtest_completion_state(
              id, sequence, status, source, completed_at, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              sequence = excluded.sequence,
              status = excluded.status,
              source = excluded.source,
              completed_at = excluded.completed_at,
              updated_at = excluded.updated_at
            """,
            (sequence, normalized_status, normalized_source, completed_ts, updated_ts),
        )

    return {
        "sequence": sequence,
        "status": normalized_status,
        "source": normalized_source,
        "completed_at": completed_ts,
        "updated_at": updated_ts,
    }


def log_notification(channel: str, event_type: str, summary: str) -> None:
    if _use_shared_database():
        run_migrations()
        with _DB_LOCK, get_engine().begin() as connection:
            connection.execute(
                insert(NOTIFICATION_EVENTS).values(
                    created_at=datetime.now(timezone.utc),
                    channel=str(channel or "unknown"),
                    event_type=str(event_type or "unknown"),
                    summary=str(summary or ""),
                )
            )
        return

    with _DB_LOCK, _connect_sqlite() as connection:
        connection.execute(
            "INSERT INTO notification_log(timestamp, channel, event_type, summary) VALUES (?, ?, ?, ?)",
            (time.time(), channel, event_type, summary),
        )
        connection.execute(
            "DELETE FROM notification_log WHERE id NOT IN (SELECT id FROM notification_log ORDER BY id DESC LIMIT 200)"
        )


def get_notification_log(limit: int = 50) -> list[dict]:
    if _use_shared_database():
        return list_notification_events(limit=limit)

    with _connect_sqlite() as connection:
        rows = connection.execute(
            "SELECT timestamp, channel, event_type, summary FROM notification_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "timestamp": row["timestamp"],
            "channel": row["channel"],
            "event_type": row["event_type"],
            "summary": row["summary"],
        }
        for row in rows
    ]
