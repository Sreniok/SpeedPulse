#!/usr/bin/env python3
"""Shared helpers for loading JSON config files."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

_DEFAULT_CONFIG_NAME = "config.json"
_DATA_ROOT_ENV = "APP_DATA_DIR"
_CONFIG_PATH_ENV = "CONFIG_PATH"


def _resolve_base_path(script_file: str | Path) -> Path:
    return Path(script_file).resolve().parent


def resolve_runtime_root(script_file: str | Path, env_name: str = _DATA_ROOT_ENV) -> Path:
    """Resolve the runtime data root relative to the script location."""
    raw_value = os.getenv(env_name, "").strip()
    if not raw_value:
        return _resolve_base_path(script_file)

    path = Path(raw_value)
    if path.is_absolute():
        return path
    return _resolve_base_path(script_file) / path


def resolve_runtime_path(script_file: str | Path, path_value: str, env_name: str = _DATA_ROOT_ENV) -> Path:
    """Resolve a runtime file path against the configured data root."""
    path = Path(path_value)
    if path.is_absolute():
        return path
    return resolve_runtime_root(script_file, env_name=env_name) / path


def resolve_config_path(script_file: str | Path, config_name: str = _DEFAULT_CONFIG_NAME) -> Path:
    """Resolve config path relative to the script file."""
    raw_value = os.getenv(_CONFIG_PATH_ENV, "").strip()
    if raw_value:
        path = Path(raw_value)
        if path.is_absolute():
            return path
        return _resolve_base_path(script_file) / path
    return resolve_runtime_path(script_file, config_name)


def load_json_config(script_file: str | Path, config_name: str = _DEFAULT_CONFIG_NAME) -> dict:
    """Load config as JSON, raising standard file/json exceptions on failure."""
    config_path = resolve_config_path(script_file, config_name=config_name)
    with config_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Config at {config_path} must be a JSON object")
    return payload


def load_json_config_or_exit(
    script_file: str | Path,
    *,
    missing_message: str,
    on_missing: Callable[[str], None] | None = None,
    exit_code: int = 1,
    config_name: str = _DEFAULT_CONFIG_NAME,
) -> dict:
    """Load config file or terminate with ``SystemExit`` when it is missing."""
    config_path = resolve_config_path(script_file, config_name=config_name)
    if not config_path.exists():
        if on_missing is not None:
            on_missing(missing_message)
        raise SystemExit(exit_code)
    with config_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise SystemExit(exit_code)
    return payload
