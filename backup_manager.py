"""Encrypted backup and restore for SpeedPulse configuration and data."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from version import __version__

SCRIPT_DIR = Path(__file__).resolve().parent

_BACKUP_EXT = ".speedpulse-backup"
_MANIFEST_NAME = "manifest.json"
_CONFIG_NAME = "config.json"
_ENV_NAME = ".env"
_ENV_BACKUP_NAME = "env_backup.json"
_RUNTIME_DB_NAME = "runtime_state.sqlite3"
_STATE_DB_DEFAULT = "Archive/runtime_state.sqlite3"
_LOG_DIR_NAME = "Log"
_LOG_PATTERN = "speed_log_week_*.txt"
_DEFAULT_BACKUP_DIR = "Backups"
_DEFAULT_MAX_BACKUPS = 10
_SALT_LENGTH = 16
_KDF_ITERATIONS = 480_000
# Only these .env keys are included in backups (secrets like APP_SECRET_KEY
# and AUTH_SALT are excluded — they are regenerated on each installation).
_ENV_KEYS_TO_BACKUP = frozenset({
    "TZ",
    "APP_TIMEZONE",
    "DASHBOARD_PORT",
    "DASHBOARD_LOGIN_EMAIL",
    "DASHBOARD_PASSWORD_HASH",
    "SESSION_COOKIE_SECURE",
    "SMTP_PASSWORD",
    "EMAIL_TO",
    "RECOVERY_EMAIL",
    "HEALTH_CHECK_TIME",
    "LOG_ROTATION_TIME",
    "RUN_STARTUP_SPEEDTEST",
    "MANUAL_SPEEDTEST_COOLDOWN_SECONDS",
    "BACKUP_PASSWORD",
})

_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_\-]+\.speedpulse-backup$")


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_KDF_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def _encrypt(data: bytes, password: str) -> bytes:
    salt = os.urandom(_SALT_LENGTH)
    key = _derive_key(password, salt)
    token = Fernet(key).encrypt(data)
    return salt + token


def _decrypt(blob: bytes, password: str) -> bytes:
    if len(blob) <= _SALT_LENGTH:
        raise ValueError("Backup file is too small or corrupted.")
    salt = blob[:_SALT_LENGTH]
    token = blob[_SALT_LENGTH:]
    key = _derive_key(password, salt)
    try:
        return Fernet(key).decrypt(token)
    except InvalidToken:
        raise ValueError("Wrong backup password or corrupted file.")


def _resolve_path(value: str) -> Path:
    p = Path(value)
    if p.is_absolute():
        return p
    return SCRIPT_DIR / p


def _load_config(config: dict | None = None) -> dict:
    """Return provided config or load config.json from SCRIPT_DIR."""
    if config is not None:
        return config
    config_path = SCRIPT_DIR / _CONFIG_NAME
    if not config_path.is_file():
        return {}
    with config_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _runtime_state_db_path() -> Path:
    raw_path = os.getenv("STATE_DB_PATH", _STATE_DB_DEFAULT).strip()
    return _resolve_path(raw_path)


def _backup_files(backup_dir: Path, *, newest_first: bool) -> list[Path]:
    return sorted(
        backup_dir.glob(f"*{_BACKUP_EXT}"),
        key=lambda file_path: file_path.stat().st_mtime,
        reverse=newest_first,
    )


def _read_manifest_from_archive(zf: zipfile.ZipFile, names: list[str]) -> dict:
    if _MANIFEST_NAME not in names:
        raise ValueError("Invalid backup: manifest not found.")
    return json.loads(zf.read(_MANIFEST_NAME))


def _restore_log_files(zf: zipfile.ZipFile, names: list[str], summary: dict) -> int:
    log_dir = SCRIPT_DIR / _LOG_DIR_NAME
    restored_count = 0
    for name in names:
        if not (name.startswith(f"{_LOG_DIR_NAME}/") and name.endswith(".txt")):
            continue
        safe_name = Path(name).name
        if not safe_name or ".." in name:
            summary["warnings"].append(f"Skipped suspicious path: {name}")
            continue
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / safe_name).write_bytes(zf.read(name))
        restored_count += 1
    return restored_count


def _read_env_subset(env_path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not env_path.is_file():
        return result
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key in _ENV_KEYS_TO_BACKUP:
            value = value.strip().strip('"').strip("'")
            result[key] = value
    return result


def create_backup(password: str, include_logs: bool = True) -> tuple[bytes, str]:
    """Create an encrypted backup archive.

    Returns ``(encrypted_bytes, suggested_filename)``.
    """
    if not password or len(password) < 6:
        raise ValueError("Backup password must be at least 6 characters.")

    buf = io.BytesIO()
    manifest: dict = {
        "version": __version__,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "include_logs": include_logs,
        "files": [],
    }

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # config.json
        config_path = SCRIPT_DIR / _CONFIG_NAME
        if config_path.is_file():
            zf.write(config_path, _CONFIG_NAME)
            manifest["files"].append(_CONFIG_NAME)

        # .env subset
        env_path = SCRIPT_DIR / _ENV_NAME
        env_data = _read_env_subset(env_path)
        if env_data:
            zf.writestr(_ENV_BACKUP_NAME, json.dumps(env_data, indent=2))
            manifest["files"].append(_ENV_BACKUP_NAME)

        # SQLite database
        db_path = _runtime_state_db_path()
        if db_path.is_file():
            zf.write(db_path, _RUNTIME_DB_NAME)
            manifest["files"].append(_RUNTIME_DB_NAME)

        # Speed test logs
        if include_logs:
            log_dir = SCRIPT_DIR / _LOG_DIR_NAME
            if log_dir.is_dir():
                for log_file in sorted(log_dir.glob(_LOG_PATTERN)):
                    arcname = f"{_LOG_DIR_NAME}/{log_file.name}"
                    zf.write(log_file, arcname)
                    manifest["files"].append(arcname)

        zf.writestr(_MANIFEST_NAME, json.dumps(manifest, indent=2))

    encrypted = _encrypt(buf.getvalue(), password)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    filename = f"speedpulse_{ts}{_BACKUP_EXT}"
    return encrypted, filename


def validate_backup(data: bytes, password: str) -> dict:
    """Decrypt and return the manifest without restoring anything."""
    decrypted = _decrypt(data, password)
    with zipfile.ZipFile(io.BytesIO(decrypted), "r") as zf:
        names = zf.namelist()
        return _read_manifest_from_archive(zf, names)


def restore_backup(data: bytes, password: str) -> dict:
    """Decrypt and restore all data from a backup archive.

    Returns a summary dict of what was restored.
    """
    decrypted = _decrypt(data, password)
    summary: dict = {"restored": [], "skipped": [], "warnings": []}

    with zipfile.ZipFile(io.BytesIO(decrypted), "r") as zf:
        names = zf.namelist()
        summary["manifest"] = _read_manifest_from_archive(zf, names)

        # config.json
        if _CONFIG_NAME in names:
            content = zf.read(_CONFIG_NAME)
            json.loads(content)  # validate JSON
            (SCRIPT_DIR / _CONFIG_NAME).write_bytes(content)
            summary["restored"].append(_CONFIG_NAME)

        # .env subset — merge into existing .env, preserving security tokens
        if _ENV_BACKUP_NAME in names:
            restored_env = json.loads(zf.read(_ENV_BACKUP_NAME))
            _merge_env_values(restored_env)
            summary["restored"].append(".env settings")

        # SQLite database
        if _RUNTIME_DB_NAME in names:
            db_path = _runtime_state_db_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db_path.write_bytes(zf.read(_RUNTIME_DB_NAME))
            summary["restored"].append(_RUNTIME_DB_NAME)

        # Speed test logs
        log_count = _restore_log_files(zf, names, summary)
        if log_count:
            summary["restored"].append(f"{log_count} speed log files")

    return summary


def _merge_env_values(restored: dict[str, str]) -> None:
    """Merge restored .env values into the current .env file.

    Only keys in ``_ENV_KEYS_TO_BACKUP`` are written.  Security tokens
    (APP_SECRET_KEY, AUTH_SALT, etc.) are never touched.
    """
    env_path = SCRIPT_DIR / _ENV_NAME
    lines: list[str] = []
    if env_path.is_file():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    first_position: dict[str, int] = {}
    for idx, raw in enumerate(lines):
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in raw:
            continue
        key = raw.split("=", 1)[0].strip()
        if key and key not in first_position:
            first_position[key] = idx

    for key, value in restored.items():
        if key not in _ENV_KEYS_TO_BACKUP:
            continue
        sanitized = str(value).replace("\n", "").replace("\r", "").strip()
        escaped = sanitized.replace("\\", "\\\\").replace('"', '\\"')
        line = f'{key}="{escaped}"'
        if key in first_position:
            lines[first_position[key]] = line
        else:
            lines.append(line)

    rendered = "\n".join(lines).rstrip() + "\n"
    env_path.write_text(rendered, encoding="utf-8")


# ── Backup directory management ──────────────────────────────────


def _backup_dir(config: dict | None = None) -> Path:
    loaded_config = _load_config(config)
    raw = loaded_config.get("backup", {}).get("backup_directory", _DEFAULT_BACKUP_DIR)
    return _resolve_path(raw)


def save_backup_to_path(
    encrypted_bytes: bytes, filename: str, config: dict | None = None
) -> Path:
    """Write an encrypted backup to the configured backup directory.

    Enforces ``max_backups`` by deleting the oldest files when exceeded.
    """
    backup_dir = _backup_dir(config)
    backup_dir.mkdir(parents=True, exist_ok=True)

    dest = backup_dir / filename
    dest.write_bytes(encrypted_bytes)

    loaded_config = _load_config(config)
    max_backups = int(loaded_config.get("backup", {}).get("max_backups", _DEFAULT_MAX_BACKUPS))
    _enforce_max_backups(backup_dir, max_backups)

    return dest


def list_backups(config: dict | None = None) -> list[dict]:
    """Return metadata for every backup in the configured directory."""
    backup_dir = _backup_dir(config)
    if not backup_dir.is_dir():
        return []

    result: list[dict] = []
    for p in _backup_files(backup_dir, newest_first=True):
        stat = p.stat()
        result.append({
            "filename": p.name,
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return result


def get_backup_path(filename: str, config: dict | None = None) -> Path | None:
    """Resolve and validate a backup filename (preventing path traversal)."""
    if not _SAFE_FILENAME_RE.match(filename):
        return None
    backup_dir = _backup_dir(config)
    path = backup_dir / filename
    # Ensure the resolved path is actually inside the backup directory
    try:
        path.resolve().relative_to(backup_dir.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path


def delete_backup(filename: str, config: dict | None = None) -> bool:
    """Delete a backup file.  Returns True if deleted."""
    path = get_backup_path(filename, config)
    if path is None:
        return False
    path.unlink()
    return True


def _enforce_max_backups(backup_dir: Path, max_backups: int) -> None:
    if max_backups <= 0:
        return
    files = _backup_files(backup_dir, newest_first=False)
    while len(files) > max_backups:
        oldest = files.pop(0)
        oldest.unlink(missing_ok=True)


# ── Scheduled backup runner ──────────────────────────────────────


def run_scheduled_backup() -> str:
    """Create a scheduled backup using the password from BACKUP_PASSWORD env var.

    Returns a status message suitable for logging.
    """
    password = os.getenv("BACKUP_PASSWORD", "").strip()
    if not password or len(password) < 6:
        return "Scheduled backup skipped: BACKUP_PASSWORD not set or too short (min 6 chars)."

    config = _load_config()

    backup_cfg = config.get("backup", {})
    include_logs = backup_cfg.get("scheduled_backup_include_logs", True)

    encrypted, filename = create_backup(password, include_logs=include_logs)
    dest = save_backup_to_path(encrypted, filename, config)
    return f"Scheduled backup created: {dest.name} ({len(encrypted)} bytes)"
