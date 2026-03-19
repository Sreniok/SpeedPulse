#!/usr/bin/env python3
"""Database-backed storage for speed test measurements and notification events."""

from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    create_engine,
    desc,
    insert,
    select,
)
from sqlalchemy.engine import Engine, RowMapping
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

DATABASE_URL_ENV = "DATABASE_URL"
SECRETS_MASTER_KEY_ENV = "SECRETS_MASTER_KEY"
BASELINE_MIGRATION = "001_measurements_baseline"
SECRETS_MIGRATION = "002_encrypted_app_secrets"
RUNTIME_STATE_MIGRATION = "003_runtime_state_postgres"

METADATA = MetaData()

SCHEMA_MIGRATIONS = Table(
    "schema_migrations",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("version", String(128), nullable=False, unique=True),
    Column("description", Text, nullable=False),
    Column("applied_at", DateTime(timezone=True), nullable=False),
)

SPEED_TESTS = Table(
    "speed_tests",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", DateTime(timezone=False), nullable=False),
    Column("source", String(16), nullable=False),
    Column("server", Text, nullable=False),
    Column("server_id", String(64), nullable=False, default=""),
    Column("isp", Text, nullable=False),
    Column("ip_address", String(128), nullable=False, default=""),
    Column("download_mbps", Numeric(10, 2), nullable=False),
    Column("upload_mbps", Numeric(10, 2), nullable=False),
    Column("ping_ms", Numeric(10, 2), nullable=False),
    Column("jitter_ms", Numeric(10, 2), nullable=False),
    Column("packet_loss_percent", Numeric(10, 2), nullable=False),
    Column("result_url", Text, nullable=False, default=""),
    Column("import_source", String(32), nullable=False, default="app"),
    Column("fingerprint", String(64), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Index("ix_speed_tests_recorded_at", SPEED_TESTS.c.recorded_at)
Index("ix_speed_tests_source_recorded_at", SPEED_TESTS.c.source, SPEED_TESTS.c.recorded_at)

NOTIFICATION_EVENTS = Table(
    "notification_events",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("channel", String(32), nullable=False),
    Column("event_type", String(64), nullable=False),
    Column("summary", Text, nullable=False),
)

Index("ix_notification_events_created_at", NOTIFICATION_EVENTS.c.created_at)

APP_SECRETS = Table(
    "app_secrets",
    METADATA,
    Column("name", String(128), primary_key=True),
    Column("value_encrypted", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

RUNTIME_METADATA = Table(
    "runtime_metadata",
    METADATA,
    Column("key", String(128), primary_key=True),
    Column("value", Text, nullable=False),
)

RUNTIME_LOGIN_STATE = Table(
    "runtime_login_state",
    METADATA,
    Column("client_ip", String(128), primary_key=True),
    Column("attempts_json", Text, nullable=False, default="[]"),
    Column("blocked_until", Float, nullable=False, default=0.0),
)

RUNTIME_RESET_TOKENS = Table(
    "runtime_reset_tokens",
    METADATA,
    Column("token", String(255), primary_key=True),
    Column("login_email", Text, nullable=False),
    Column("expires", Float, nullable=False),
)

Index("ix_runtime_reset_tokens_expires", RUNTIME_RESET_TOKENS.c.expires)

RUNTIME_MANUAL_RUN_STATE = Table(
    "runtime_manual_run_state",
    METADATA,
    Column("id", Integer, primary_key=True),
    Column("payload_json", Text, nullable=False),
    Column("last_manual_speedtest_at", Float, nullable=False, default=0.0),
)

RUNTIME_SPEEDTEST_COMPLETION_STATE = Table(
    "runtime_speedtest_completion_state",
    METADATA,
    Column("id", Integer, primary_key=True),
    Column("sequence", Integer, nullable=False, default=0),
    Column("status", String(32), nullable=False, default="unknown"),
    Column("source", String(32), nullable=False, default="unknown"),
    Column("completed_at", Float, nullable=False, default=0.0),
    Column("updated_at", Float, nullable=False, default=0.0),
)


def _normalize_database_url(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if not value:
        return ""
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+psycopg://", 1)
    if value.startswith("postgresql://") and "+psycopg" not in value:
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


def database_url() -> str:
    return _normalize_database_url(os.getenv(DATABASE_URL_ENV, ""))


def database_enabled() -> bool:
    return bool(database_url())


def _secrets_master_key() -> str:
    return str(os.getenv(SECRETS_MASTER_KEY_ENV, "") or "").strip()


def encrypted_secret_store_enabled() -> bool:
    return database_enabled() and bool(_secrets_master_key())


@lru_cache(maxsize=8)
def _engine_for_url(url: str) -> Engine:
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)


def get_engine() -> Engine:
    url = database_url()
    if not url:
        raise RuntimeError(f"{DATABASE_URL_ENV} is not configured")
    return _engine_for_url(url)


@lru_cache(maxsize=8)
def _fernet_for_key(raw_key: str) -> Fernet:
    derived = base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode("utf-8")).digest())
    return Fernet(derived)


def _secret_cipher() -> Fernet:
    raw_key = _secrets_master_key()
    if not raw_key:
        raise RuntimeError(f"{SECRETS_MASTER_KEY_ENV} is not configured")
    return _fernet_for_key(raw_key)


def _safe_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(round(_as_float(value), 2)))
    except (ArithmeticError, TypeError, ValueError):
        return Decimal("0.00")


def _as_float(value: object) -> float:
    try:
        return float(str(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _normalize_timestamp(value: datetime | None = None) -> datetime:
    source = value or datetime.now()
    return source.replace(second=0, microsecond=0, tzinfo=None)


def _speed_test_fingerprint(payload: dict[str, object]) -> str:
    raw_timestamp = payload.get("timestamp")
    timestamp = _normalize_timestamp(raw_timestamp if isinstance(raw_timestamp, datetime) else None)
    raw = "|".join(
        [
            timestamp.isoformat(timespec="minutes"),
            str(payload.get("source", "scheduled") or "scheduled"),
            str(payload.get("server", "Unknown") or "Unknown"),
            str(payload.get("server_id", "") or ""),
            str(payload.get("isp", "Unknown") or "Unknown"),
            str(payload.get("ip_address", "") or ""),
            f"{_as_float(payload.get('download_mbps', 0.0)):.2f}",
            f"{_as_float(payload.get('upload_mbps', 0.0)):.2f}",
            f"{_as_float(payload.get('ping_ms', 0.0)):.2f}",
            f"{_as_float(payload.get('jitter_ms', 0.0)):.2f}",
            f"{_as_float(payload.get('packet_loss_percent', 0.0)):.2f}",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def run_migrations() -> list[str]:
    if not database_enabled():
        return []

    applied: list[str] = []
    now = datetime.now(timezone.utc)
    with get_engine().begin() as connection:
        SCHEMA_MIGRATIONS.create(connection, checkfirst=True)
        existing = {
            str(row["version"])
            for row in connection.execute(select(SCHEMA_MIGRATIONS.c.version)).mappings()
        }
        if BASELINE_MIGRATION not in existing:
            METADATA.create_all(connection, checkfirst=True)
            connection.execute(
                insert(SCHEMA_MIGRATIONS).values(
                    version=BASELINE_MIGRATION,
                    description="Initial PostgreSQL-backed measurement storage",
                    applied_at=now,
                )
            )
            applied.append(BASELINE_MIGRATION)
        else:
            METADATA.create_all(connection, checkfirst=True)
        if SECRETS_MIGRATION not in existing:
            APP_SECRETS.create(connection, checkfirst=True)
            connection.execute(
                insert(SCHEMA_MIGRATIONS).values(
                    version=SECRETS_MIGRATION,
                    description="Encrypted application secret storage",
                    applied_at=now,
                )
            )
            applied.append(SECRETS_MIGRATION)
        if RUNTIME_STATE_MIGRATION not in existing:
            RUNTIME_METADATA.create(connection, checkfirst=True)
            RUNTIME_LOGIN_STATE.create(connection, checkfirst=True)
            RUNTIME_RESET_TOKENS.create(connection, checkfirst=True)
            RUNTIME_MANUAL_RUN_STATE.create(connection, checkfirst=True)
            RUNTIME_SPEEDTEST_COMPLETION_STATE.create(connection, checkfirst=True)
            connection.execute(
                insert(SCHEMA_MIGRATIONS).values(
                    version=RUNTIME_STATE_MIGRATION,
                    description="Runtime/auth/session state moved to shared SQL storage",
                    applied_at=now,
                )
            )
            applied.append(RUNTIME_STATE_MIGRATION)
    return applied


def database_healthcheck() -> dict[str, str]:
    if not database_enabled():
        return {"status": "disabled", "message": f"{DATABASE_URL_ENV} is not configured"}

    try:
        run_migrations()
        with get_engine().connect() as connection:
            connection.execute(select(SPEED_TESTS.c.id).limit(1))
        return {"status": "ok", "message": "database reachable"}
    except SQLAlchemyError as exc:
        return {"status": "error", "message": str(exc)}


def get_app_secret(name: str) -> str:
    if not encrypted_secret_store_enabled():
        return ""

    run_migrations()
    with get_engine().connect() as connection:
        row = connection.execute(
            select(APP_SECRETS.c.value_encrypted).where(APP_SECRETS.c.name == str(name or ""))
        ).mappings().first()

    if not row:
        return ""

    encrypted_value = str(row["value_encrypted"] or "")
    if not encrypted_value:
        return ""

    try:
        decrypted = _secret_cipher().decrypt(encrypted_value.encode("utf-8"))
    except (InvalidToken, ValueError) as exc:
        raise RuntimeError(f"Failed to decrypt app secret '{name}'") from exc
    return decrypted.decode("utf-8")


def has_app_secret(name: str) -> bool:
    return bool(get_app_secret(name))


def set_app_secret(name: str, value: str) -> bool:
    if not encrypted_secret_store_enabled():
        return False

    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise ValueError("Secret name is required")

    normalized_value = str(value or "")
    if not normalized_value:
        delete_app_secret(normalized_name)
        return True

    run_migrations()
    now = datetime.now(timezone.utc)
    encrypted_value = _secret_cipher().encrypt(normalized_value.encode("utf-8")).decode("utf-8")
    payload = {
        "name": normalized_name,
        "value_encrypted": encrypted_value,
        "created_at": now,
        "updated_at": now,
    }

    with get_engine().begin() as connection:
        existing = connection.execute(
            select(APP_SECRETS.c.name).where(APP_SECRETS.c.name == normalized_name)
        ).first()
        if existing:
            connection.execute(
                APP_SECRETS.update()
                .where(APP_SECRETS.c.name == normalized_name)
                .values(value_encrypted=encrypted_value, updated_at=now)
            )
        else:
            connection.execute(insert(APP_SECRETS).values(**payload))
    return True


def delete_app_secret(name: str) -> bool:
    if not encrypted_secret_store_enabled():
        return False

    normalized_name = str(name or "").strip()
    if not normalized_name:
        return False

    run_migrations()
    with get_engine().begin() as connection:
        result = connection.execute(APP_SECRETS.delete().where(APP_SECRETS.c.name == normalized_name))
    return bool(result.rowcount)


def _row_to_speed_test(row: RowMapping) -> dict[str, object]:
    recorded_at = row["recorded_at"]
    if not isinstance(recorded_at, datetime):
        raise ValueError("recorded_at must be a datetime")
    return {
        "timestamp": recorded_at,
        "download_mbps": _as_float(row["download_mbps"]),
        "upload_mbps": _as_float(row["upload_mbps"]),
        "ping_ms": _as_float(row["ping_ms"]),
        "jitter_ms": _as_float(row["jitter_ms"]),
        "packet_loss_percent": _as_float(row["packet_loss_percent"]),
        "server": str(row["server"] or "Unknown"),
        "isp": str(row["isp"] or "Unknown"),
        "source": str(row["source"] or "scheduled"),
        "ip_address": str(row["ip_address"] or ""),
        "server_id": str(row["server_id"] or ""),
        "result_url": str(row["result_url"] or ""),
    }


def record_speed_test(payload: dict[str, object], *, import_source: str = "app") -> bool:
    if not database_enabled():
        return False

    run_migrations()
    raw_timestamp = payload.get("timestamp")
    timestamp = _normalize_timestamp(raw_timestamp if isinstance(raw_timestamp, datetime) else None)
    normalized = {
        "recorded_at": timestamp,
        "source": str(payload.get("source", "scheduled") or "scheduled"),
        "server": str(payload.get("server", "Unknown") or "Unknown"),
        "server_id": str(payload.get("server_id", "") or ""),
        "isp": str(payload.get("isp", "Unknown") or "Unknown"),
        "ip_address": str(payload.get("ip_address", "") or ""),
        "download_mbps": _safe_decimal(payload.get("download_mbps", 0.0)),
        "upload_mbps": _safe_decimal(payload.get("upload_mbps", 0.0)),
        "ping_ms": _safe_decimal(payload.get("ping_ms", 0.0)),
        "jitter_ms": _safe_decimal(payload.get("jitter_ms", 0.0)),
        "packet_loss_percent": _safe_decimal(payload.get("packet_loss_percent", 0.0)),
        "result_url": str(payload.get("result_url", "") or ""),
        "import_source": str(import_source or "app"),
        "fingerprint": _speed_test_fingerprint({**payload, "timestamp": timestamp}),
        "created_at": datetime.now(timezone.utc),
    }

    try:
        with get_engine().begin() as connection:
            connection.execute(insert(SPEED_TESTS).values(**normalized))
        return True
    except IntegrityError:
        return False


def list_speed_tests(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int | None = None,
    descending: bool = False,
) -> list[dict[str, object]]:
    if not database_enabled():
        return []

    run_migrations()
    stmt = select(SPEED_TESTS)
    if start is not None:
        stmt = stmt.where(SPEED_TESTS.c.recorded_at >= _normalize_timestamp(start))
    if end is not None:
        stmt = stmt.where(SPEED_TESTS.c.recorded_at <= _normalize_timestamp(end))
    order_column = desc(SPEED_TESTS.c.recorded_at) if descending else SPEED_TESTS.c.recorded_at
    stmt = stmt.order_by(order_column, SPEED_TESTS.c.id if not descending else desc(SPEED_TESTS.c.id))
    if limit is not None:
        stmt = stmt.limit(max(1, int(limit)))

    with get_engine().connect() as connection:
        rows = connection.execute(stmt).mappings().all()
    return [_row_to_speed_test(row) for row in rows]


def record_notification_event(channel: str, event_type: str, summary: str) -> bool:
    if not database_enabled():
        return False

    run_migrations()
    payload = {
        "created_at": datetime.now(timezone.utc),
        "channel": str(channel or "unknown"),
        "event_type": str(event_type or "unknown"),
        "summary": str(summary or "").strip(),
    }
    with get_engine().begin() as connection:
        connection.execute(insert(NOTIFICATION_EVENTS).values(**payload))
    return True


def list_notification_events(limit: int = 50) -> list[dict[str, object]]:
    if not database_enabled():
        return []

    run_migrations()
    stmt = (
        select(NOTIFICATION_EVENTS)
        .order_by(desc(NOTIFICATION_EVENTS.c.created_at), desc(NOTIFICATION_EVENTS.c.id))
        .limit(max(1, int(limit)))
    )
    with get_engine().connect() as connection:
        rows = connection.execute(stmt).mappings().all()

    result: list[dict[str, object]] = []
    for row in rows:
        created_at = row["created_at"]
        if not isinstance(created_at, datetime):
            created_at = datetime.now(timezone.utc)
        result.append(
            {
                "timestamp": created_at.timestamp(),
                "channel": str(row["channel"] or "unknown"),
                "event_type": str(row["event_type"] or "unknown"),
                "summary": str(row["summary"] or ""),
            }
        )
    return result
