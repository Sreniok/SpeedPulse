#!/usr/bin/env python3
"""Apply DB migrations and optionally import legacy speed test logs."""

from __future__ import annotations

import argparse
from pathlib import Path

from config_loader import load_json_config
from log_parser import load_all_log_entries
from measurement_repository import measurement_log_dir
from measurement_store import database_enabled, record_speed_test, run_migrations


def import_logs(config: dict) -> dict[str, int]:
    log_dir = measurement_log_dir(config)
    entries = load_all_log_entries(log_dir)
    inserted = 0
    skipped = 0

    for entry in entries:
        stored = record_speed_test(
            {
                "timestamp": entry["timestamp"],
                "source": entry.get("source", "scheduled"),
                "server": entry.get("server", "Unknown"),
                "server_id": entry.get("server_id", ""),
                "isp": entry.get("isp", "Unknown"),
                "ip_address": entry.get("ip_address", ""),
                "download_mbps": entry.get("download_mbps", 0.0),
                "upload_mbps": entry.get("upload_mbps", 0.0),
                "ping_ms": entry.get("ping_ms", 0.0),
                "jitter_ms": entry.get("jitter_ms", 0.0),
                "packet_loss_percent": entry.get("packet_loss_percent", 0.0),
                "result_url": entry.get("result_url", ""),
            },
            import_source="log_import",
        )
        if stored:
            inserted += 1
        else:
            skipped += 1

    return {"files": len(list(Path(log_dir).glob("speed_log_week_*.txt"))), "entries": len(entries), "inserted": inserted, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SpeedPulse DB migrations.")
    parser.add_argument("--import-logs", action="store_true", help="Import legacy log-based measurements after migrations.")
    args = parser.parse_args()

    if not database_enabled():
        print("DATABASE_URL is not configured; skipping DB migration.")
        return 0

    applied = run_migrations()
    if applied:
        print(f"Applied migrations: {', '.join(applied)}")
    else:
        print("DB schema already up to date.")

    if args.import_logs:
        config = load_json_config(__file__)
        summary = import_logs(config)
        print(
            "Imported log history: "
            f"{summary['inserted']} inserted, "
            f"{summary['skipped']} skipped, "
            f"{summary['entries']} entries from {summary['files']} files."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
