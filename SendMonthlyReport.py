#!/usr/bin/env python3
"""Send monthly SpeedPulse report email (previous calendar month)."""

from __future__ import annotations

import smtplib
import sys
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config_loader import load_json_config
from logger_setup import get_logger
from mail_settings import load_mail_settings
from measurement_repository import load_measurement_entries
from measurement_store import record_notification_event
from push_notifications import send_ntfy_event, send_webhook_event
from reporting import build_report_html, resolve_report_theme_id

log = get_logger("SendMonthlyReport")


def load_config() -> dict:
    return load_json_config(__file__)


def _month_window(reference: datetime | None = None) -> tuple[datetime, datetime]:
    now = reference or datetime.now()
    first_current = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_end = first_current - timedelta(seconds=1)
    month_start = month_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return month_start, month_end


def _previous_month_window(month_start: datetime) -> tuple[datetime, datetime]:
    previous_end = month_start - timedelta(seconds=1)
    previous_start = previous_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return previous_start, previous_end


def _entries_in_range(entries: list[dict], start: datetime, end: datetime) -> list[dict]:
    return [entry for entry in entries if start <= entry.get("timestamp", datetime.min) <= end]


def send_email(config: dict, subject: str, body_html: str) -> bool:
    try:
        mail = load_mail_settings(config)
    except Exception as exc:
        log.error("Failed to load mail settings: %s", exc)
        return False

    message = MIMEMultipart("alternative")
    message["From"] = mail.from_addr
    message["To"] = mail.to_addr
    message["Subject"] = subject
    message.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        server: smtplib.SMTP | smtplib.SMTP_SSL
        if mail.smtp_port == 465:
            server = smtplib.SMTP_SSL(mail.smtp_server, mail.smtp_port, timeout=60)
        else:
            server = smtplib.SMTP(mail.smtp_server, mail.smtp_port, timeout=60)
            server.starttls()

        with server:
            server.login(mail.smtp_username, mail.smtp_password)
            server.send_message(message)

        log.info("Monthly report email sent")
        return True
    except smtplib.SMTPException as exc:
        log.error("SMTP error while sending monthly report: %s", exc)
        return False
    except Exception as exc:
        log.error("Failed to send monthly report: %s", exc)
        return False


def main() -> int:
    config = load_config()

    all_entries = load_measurement_entries(config)
    month_start, month_end = _month_window()
    previous_start, previous_end = _previous_month_window(month_start)

    entries = _entries_in_range(all_entries, month_start, month_end)
    previous_entries = _entries_in_range(all_entries, previous_start, previous_end)
    if not entries:
        log.warning("No speed test data found for %s", month_start.strftime("%B %Y"))
        return 1

    month_label = month_start.strftime("%B %Y")
    theme_id = resolve_report_theme_id(config)

    body = build_report_html(
        config,
        entries,
        report_title=f"Monthly Speed Report - {month_label}",
        range_label=month_label,
        theme_id=theme_id,
        previous_entries=previous_entries,
    )

    subject = f"SpeedPulse Monthly Report - {month_label}"
    email_success = send_email(config, subject, body)
    if not email_success:
        return 1

    summary = f"{month_label} report sent ({len(entries)} tests)"
    try:
        from state_store import log_notification

        log_notification("email", "monthly_report", summary)
        record_notification_event("email", "monthly_report", summary)

        webhook_success = send_webhook_event(
            config,
            "monthly_report",
            "SpeedPulse monthly report",
            summary,
            payload_extra={"month": month_label, "tests": len(entries), "theme": theme_id},
            logger=log,
        )
        ntfy_success = send_ntfy_event(
            config,
            "monthly_report",
            "Monthly report",
            summary,
            priority="3",
            tags="email,calendar",
            logger=log,
        )

        if webhook_success:
            log_notification("webhook", "monthly_report", summary)
            record_notification_event("webhook", "monthly_report", summary)
        if ntfy_success:
            log_notification("ntfy", "monthly_report", summary)
            record_notification_event("ntfy", "monthly_report", summary)
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
