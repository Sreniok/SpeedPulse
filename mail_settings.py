#!/usr/bin/env python3
"""Shared helpers for loading email settings from environment/config."""

from __future__ import annotations

import os
from dataclasses import dataclass

from measurement_store import get_app_secret


@dataclass
class MailSettings:
    smtp_server: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    from_addr: str
    to_addr: str


def load_mail_settings(config: dict) -> MailSettings:
    """Load SMTP/email settings from .env with config.json fallback."""
    email_cfg = config.get("email", {})

    smtp_server = os.getenv("SMTP_SERVER", email_cfg.get("smtp_server", ""))
    smtp_port = int(os.getenv("SMTP_PORT", str(email_cfg.get("smtp_port", 465))))
    smtp_username = os.getenv("SMTP_USERNAME", email_cfg.get("from", ""))
    smtp_password = get_app_secret("smtp_password") or os.getenv("SMTP_PASSWORD", "")
    from_addr = os.getenv("EMAIL_FROM", email_cfg.get("from", smtp_username))
    to_addr = os.getenv("EMAIL_TO", email_cfg.get("to", ""))

    if not smtp_password:
        raise RuntimeError("SMTP password is required (set it in Settings or SMTP_PASSWORD in .env)")

    if not smtp_username:
        raise RuntimeError("SMTP_USERNAME is required (set it in .env)")

    if not smtp_server:
        raise RuntimeError("SMTP_SERVER is required")

    if not from_addr:
        raise RuntimeError("EMAIL_FROM is required")

    if not to_addr:
        raise RuntimeError("EMAIL_TO is required")

    return MailSettings(
        smtp_server=smtp_server,
        smtp_port=smtp_port,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        from_addr=from_addr,
        to_addr=to_addr,
    )
