#!/usr/bin/env python3
"""Shared helpers for loading JSON config files."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

_DEFAULT_CONFIG_NAME = "config.json"


def resolve_config_path(script_file: str | Path, config_name: str = _DEFAULT_CONFIG_NAME) -> Path:
    """Resolve config path relative to the script file."""
    return Path(script_file).resolve().parent / config_name


def load_json_config(script_file: str | Path, config_name: str = _DEFAULT_CONFIG_NAME) -> dict:
    """Load config as JSON, raising standard file/json exceptions on failure."""
    config_path = resolve_config_path(script_file, config_name=config_name)
    with config_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


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
        return json.load(handle)
