#!/usr/bin/env python3
"""Shared measurement repository with DB-first, log fallback loading."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from config_loader import resolve_runtime_path
from log_parser import load_all_log_entries
from measurement_store import list_speed_tests


def measurement_log_dir(config: dict) -> Path:
    return resolve_runtime_path(__file__, config.get("paths", {}).get("log_directory", "Log"))


def load_measurement_entries(config: dict) -> list[dict]:
    db_entries = list_speed_tests()
    if db_entries:
        return db_entries
    return load_all_log_entries(measurement_log_dir(config))


def load_measurement_entries_in_range(config: dict, start: datetime, end: datetime) -> list[dict]:
    db_entries = list_speed_tests(start=start, end=end)
    if db_entries:
        return db_entries
    return [
        entry
        for entry in load_all_log_entries(measurement_log_dir(config))
        if start <= entry["timestamp"] <= end
    ]
