#!/usr/bin/env python3
"""Helpers for resolving reporting windows."""

from __future__ import annotations

from datetime import datetime, timedelta


def weekly_report_window(reference: datetime | None = None) -> tuple[datetime, datetime]:
    """Return the last fully completed ISO week."""
    now = reference or datetime.now()
    current_week_start = (now - timedelta(days=now.isoweekday() - 1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    week_end = current_week_start - timedelta(seconds=1)
    week_start = current_week_start - timedelta(days=7)
    return week_start, week_end


def previous_week_window(week_start: datetime) -> tuple[datetime, datetime]:
    previous_end = week_start - timedelta(seconds=1)
    previous_start = week_start - timedelta(days=7)
    return previous_start, previous_end


def entries_in_range(entries: list[dict], start: datetime, end: datetime) -> list[dict]:
    return [
        entry
        for entry in entries
        if start <= entry.get("timestamp", datetime.min) <= end
    ]
