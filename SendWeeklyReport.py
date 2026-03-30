#!/usr/bin/env python3
"""Send weekly SpeedPulse report email."""

from __future__ import annotations

import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config_loader import load_json_config
from logger_setup import get_logger
from mail_settings import load_mail_settings
from measurement_repository import load_measurement_entries
from measurement_store import record_notification_event
from push_notifications import send_ntfy_event, send_webhook_event
from report_periods import entries_in_range, previous_week_window, weekly_report_window
from reporting import build_report_html, resolve_report_theme_id

log = get_logger("SendWeeklyReport")


def load_config() -> dict:
    return load_json_config(__file__)


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

        log.info("Weekly report email sent")
        return True
    except smtplib.SMTPException as exc:
        log.error("SMTP error while sending weekly report: %s", exc)
        return False
    except Exception as exc:
        log.error("Failed to send weekly report: %s", exc)
        return False


def main() -> int:
    config = load_config()
    all_entries = load_measurement_entries(config)

    week_start, week_end = weekly_report_window()
    previous_start, previous_end = previous_week_window(week_start)

    entries = entries_in_range(all_entries, week_start, week_end)
    if not entries:
        week_info = week_start.isocalendar()
        log.warning(
            "No speed test data found for ISO week %s-%02d (%s to %s)",
            week_info.year,
            week_info.week,
            week_start.strftime("%Y-%m-%d %H:%M"),
            week_end.strftime("%Y-%m-%d %H:%M"),
        )
        return 1

    previous_entries = entries_in_range(all_entries, previous_start, previous_end)
    theme_id = resolve_report_theme_id(config)
    week_info = week_start.isocalendar()

    report_title = f"Weekly Speed Report - Week {week_info.week} ({week_info.year})"
    range_label = (
        f"ISO week {week_info.week}, {week_info.year} "
        f"({week_start.strftime('%Y-%m-%d')} to {week_end.strftime('%Y-%m-%d')})"
    )
    body = build_report_html(
        config,
        entries,
        report_title=report_title,
        range_label=range_label,
        theme_id=theme_id,
        previous_entries=previous_entries,
    )

    subject = f"SpeedPulse Weekly Report - Week {week_info.week} ({week_info.year})"
    email_success = send_email(config, subject, body)

    if not email_success:
        return 1

    summary = f"Week {week_info.week} ({week_info.year}) report sent ({len(entries)} tests)"

    try:
        from state_store import log_notification

        log_notification("email", "weekly_report", summary)
        record_notification_event("email", "weekly_report", summary)

        webhook_success = send_webhook_event(
            config,
            "weekly_report",
            "SpeedPulse weekly report",
            summary,
            payload_extra={"week": week_info.week, "year": week_info.year, "tests": len(entries), "theme": theme_id},
            logger=log,
        )
        ntfy_success = send_ntfy_event(
            config,
            "weekly_report",
            "Weekly report",
            summary,
            priority="3",
            tags="email,chart_with_upwards_trend",
            logger=log,
        )

        if webhook_success:
            log_notification("webhook", "weekly_report", summary)
            record_notification_event("webhook", "weekly_report", summary)
        if ntfy_success:
            log_notification("ntfy", "weekly_report", summary)
            record_notification_event("ntfy", "weekly_report", summary)
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
