from __future__ import annotations

from datetime import datetime

from report_periods import entries_in_range, previous_week_window, weekly_report_window


def test_week_window_always_targets_last_completed_iso_week() -> None:
    week_start, week_end = weekly_report_window(datetime(2026, 3, 31, 9, 30))

    assert week_start == datetime(2026, 3, 23, 0, 0, 0)
    assert week_end == datetime(2026, 3, 29, 23, 59, 59)


def test_week_window_handles_year_boundary() -> None:
    week_start, week_end = weekly_report_window(datetime(2026, 1, 1, 8, 0))
    previous_start, previous_end = previous_week_window(week_start)

    assert week_start == datetime(2025, 12, 22, 0, 0, 0)
    assert week_end == datetime(2025, 12, 28, 23, 59, 59)
    assert previous_start == datetime(2025, 12, 15, 0, 0, 0)
    assert previous_end == datetime(2025, 12, 21, 23, 59, 59)


def test_entries_in_range_filters_full_week_window() -> None:
    entries = [
        {"timestamp": datetime(2026, 3, 22, 23, 59, 59), "download_mbps": 500},
        {"timestamp": datetime(2026, 3, 23, 0, 0, 0), "download_mbps": 510},
        {"timestamp": datetime(2026, 3, 26, 12, 0, 0), "download_mbps": 520},
        {"timestamp": datetime(2026, 3, 29, 23, 59, 59), "download_mbps": 530},
        {"timestamp": datetime(2026, 3, 30, 0, 0, 0), "download_mbps": 540},
    ]

    filtered = entries_in_range(
        entries,
        datetime(2026, 3, 23, 0, 0, 0),
        datetime(2026, 3, 29, 23, 59, 59),
    )

    assert [entry["download_mbps"] for entry in filtered] == [510, 520, 530]
