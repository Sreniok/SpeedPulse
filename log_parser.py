#!/usr/bin/env python3
"""Utilities for parsing speed test logs in both legacy and current formats.

Supported log formats
---------------------

**Current multi-line format** (one block per test, Upload line ends the block)::

    Date: 11-03-2026
    Time: 08:00
    Server: London - Vodafone UK
    ISP: Virgin Media
    Ping: 12.5 ms
    Jitter: 1.2 ms
    Packet Loss: 0.00%
    Download: 550.3 Mbps
    Upload: 105.8 Mbps

**Legacy one-line pipe-delimited format**::

    2025-12-05 10:30:00 | Download: 500.5 Mbps | Upload: 100.2 Mbps | Ping: 15 ms

Both formats may coexist in the same file.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_FLOAT_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _extract_float(value: str, default: float = 0.0) -> float:
    match = _FLOAT_RE.search(value)
    if not match:
        return default
    try:
        return float(match.group(1))
    except ValueError:
        return default


def _parse_pipe_line(line: str) -> dict | None:
    """Parse old one-line format:
    2025-12-05 10:30:00 | Download: 500.5 Mbps | Upload: 100.2 Mbps | Ping: 15 ms
    """
    parts = [part.strip() for part in line.split("|")]
    if len(parts) < 4:
        return None

    try:
        timestamp = datetime.strptime(parts[0], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

    return {
        "timestamp": timestamp,
        "download_mbps": _extract_float(parts[1]),
        "upload_mbps": _extract_float(parts[2]),
        "ping_ms": _extract_float(parts[3]),
        "jitter_ms": _extract_float(parts[4]) if len(parts) > 4 else 0.0,
        "packet_loss_percent": _extract_float(parts[5]) if len(parts) > 5 else 0.0,
        "server": "Unknown",
        "isp": "Unknown",
    }


def parse_weekly_log_file(log_file: Path) -> list[dict]:
    """Parse current multi-line log block format and legacy one-line format."""
    if not log_file.exists():
        return []

    entries: list[dict] = []
    current: dict[str, str] = {}

    with log_file.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue

            # Legacy one-line format support.
            if "|" in line and line[0:4].isdigit() and "Download:" in line and "Upload:" in line:
                parsed = _parse_pipe_line(line)
                if parsed:
                    entries.append(parsed)
                continue

            if line.startswith("Date:"):
                current["date"] = line.split(":", 1)[1].strip()
            elif line.startswith("Time:"):
                current["time"] = line.split(":", 1)[1].strip()
            elif line.startswith("Server:"):
                current["server"] = line.split(":", 1)[1].strip()
            elif line.startswith("ISP:"):
                current["isp"] = line.split(":", 1)[1].strip()
            elif line.startswith("Ping:"):
                current["ping"] = line
            elif line.startswith("Jitter:"):
                current["jitter"] = line
            elif line.startswith("Packet Loss:"):
                current["packet_loss"] = line
            elif line.startswith("Download:"):
                current["download"] = line
            elif line.startswith("Upload:"):
                current["upload"] = line

                date_value = current.get("date")
                time_value = current.get("time")
                if not date_value or not time_value:
                    current = {}
                    continue

                try:
                    timestamp = datetime.strptime(f"{date_value} {time_value}", "%d-%m-%Y %H:%M")
                except ValueError:
                    current = {}
                    continue

                entries.append(
                    {
                        "timestamp": timestamp,
                        "download_mbps": _extract_float(current.get("download", "")),
                        "upload_mbps": _extract_float(current.get("upload", "")),
                        "ping_ms": _extract_float(current.get("ping", "")),
                        "jitter_ms": _extract_float(current.get("jitter", "")),
                        "packet_loss_percent": _extract_float(current.get("packet_loss", "")),
                        "server": current.get("server", "Unknown"),
                        "isp": current.get("isp", "Unknown"),
                    }
                )
                current = {}

    entries.sort(key=lambda item: item["timestamp"])
    return entries


def load_all_log_entries(log_dir: Path) -> list[dict]:
    """Load all weekly logs from a directory."""
    entries: list[dict] = []
    for log_file in sorted(log_dir.glob("speed_log_week_*.txt")):
        entries.extend(parse_weekly_log_file(log_file))

    entries.sort(key=lambda item: item["timestamp"])
    return entries
