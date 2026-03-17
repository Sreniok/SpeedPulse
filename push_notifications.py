#!/usr/bin/env python3
"""Reusable webhook/ntfy notification helpers with event-level preferences."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime
from urllib.parse import quote, urlparse

from version import USER_AGENT

_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "[::1]"}
PUSH_EVENT_DEFAULTS = {
    "alert": True,
    "weekly_report": True,
    "monthly_report": True,
    "health_check": True,
}


def _log(logger: object | None, level: str, message: str, *args: object) -> None:
    if logger is None:
        return
    target = getattr(logger, level, None)
    if callable(target):
        target(message, *args)


def _validate_outbound_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https scheme")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("URL has no hostname")
    if hostname in _BLOCKED_HOSTS:
        raise ValueError("URL must not target localhost")
    if hostname.startswith("169.254.") or hostname.startswith("fe80:"):
        raise ValueError("URL must not target link-local addresses")


def effective_push_events(config: dict) -> dict[str, bool]:
    notifications = config.get("notifications", {})
    raw = notifications.get("push_events", {})

    normalized = dict(PUSH_EVENT_DEFAULTS)
    if isinstance(raw, dict):
        for key in PUSH_EVENT_DEFAULTS:
            if key in raw:
                normalized[key] = bool(raw.get(key))
    return normalized


def push_event_enabled(config: dict, event_type: str) -> bool:
    key = str(event_type or "").strip().lower()
    events = effective_push_events(config)
    return bool(events.get(key, True))


def send_webhook_event(
    config: dict,
    event_type: str,
    title: str,
    summary: str,
    payload_extra: dict | None = None,
    logger: object | None = None,
) -> bool:
    notifications = config.get("notifications", {})
    if not push_event_enabled(config, event_type):
        _log(logger, "info", "Webhook skipped: push event '%s' disabled", event_type)
        return False
    if not notifications.get("webhook_enabled", False):
        return False

    webhook_url = str(notifications.get("webhook_url", "")).strip()
    if not webhook_url:
        _log(logger, "warning", "Webhook enabled but URL is empty")
        return False

    payload = {
        "title": str(title or "SpeedPulse event"),
        "event_type": str(event_type or "event"),
        "summary": str(summary or ""),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if isinstance(payload_extra, dict):
        payload["details"] = payload_extra

    try:
        _validate_outbound_url(webhook_url)
        request = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            if int(response.status) >= 300:
                _log(logger, "error", "Webhook returned HTTP %s", response.status)
                return False
        _log(logger, "info", "Webhook event sent: %s", event_type)
        return True
    except Exception as exc:
        _log(logger, "error", "Webhook event failed: %s", exc)
        return False


def send_ntfy_event(
    config: dict,
    event_type: str,
    title: str,
    message: str,
    *,
    priority: str = "3",
    tags: str = "information",
    logger: object | None = None,
) -> bool:
    notifications = config.get("notifications", {})
    if not push_event_enabled(config, event_type):
        _log(logger, "info", "ntfy skipped: push event '%s' disabled", event_type)
        return False
    if not notifications.get("ntfy_enabled", False):
        return False

    topic = str(notifications.get("ntfy_topic", "")).strip()
    if not topic:
        _log(logger, "warning", "ntfy enabled but topic is empty")
        return False

    base_url = str(notifications.get("ntfy_server", "https://ntfy.sh")).strip() or "https://ntfy.sh"
    url = f"{base_url.rstrip('/')}/{quote(topic, safe='')}"
    try:
        _validate_outbound_url(url)
    except ValueError as exc:
        _log(logger, "error", "Invalid ntfy URL: %s", exc)
        return False

    body = str(message or "").strip() or "SpeedPulse notification"
    try:
        request = urllib.request.Request(
            url,
            data=body.encode("utf-8"),
            method="POST",
            headers={
                "Title": str(title or "SpeedPulse"),
                "Priority": str(priority or "3"),
                "Tags": str(tags or "information"),
                "User-Agent": USER_AGENT,
            },
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            if int(response.status) >= 300:
                _log(logger, "error", "ntfy returned HTTP %s", response.status)
                return False
        _log(logger, "info", "ntfy event sent: %s", event_type)
        return True
    except Exception as exc:
        _log(logger, "error", "ntfy event failed: %s", exc)
        return False

