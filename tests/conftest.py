"""Shared fixtures for the Speedtest test suite."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure the project root is importable.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def tmp_log_dir(tmp_path: Path) -> Path:
    """Return a temporary directory that mimics the Log/ folder."""
    log_dir = tmp_path / "Log"
    log_dir.mkdir()
    return log_dir


@pytest.fixture()
def sample_config(tmp_path: Path) -> dict:
    """Return a minimal config dict and write it to a temp config.json."""
    cfg = {
        "paths": {
            "speedtest_exe": "speedtest",
            "log_directory": "Log",
            "images_directory": "Images",
            "chart_base64": "chart_base64.txt",
            "error_log": "errors.log",
        },
        "thresholds": {
            "download_mbps": 500,
            "upload_mbps": 80,
            "ping_ms": 20,
            "packet_loss_percent": 1.0,
        },
        "email": {
            "from": "test@example.com",
            "to": "dest@example.com",
            "smtp_server": "smtp.example.com",
            "smtp_port": 465,
        },
        "data_retention": {
            "keep_weeks": 52,
            "keep_days": 30,
        },
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    return cfg
