#!/usr/bin/env python3
"""
contract_reminder.py - Sends a one-shot reminder that the broadband contract
is approaching its end date.  The scheduler fires this script once, on the
exact date that is `reminder_days` before the contract end.

Notification channels reuse the same infrastructure as SendAlert.py:
email (SMTP), webhook, and ntfy.
"""

from __future__ import annotations

import json
import smtplib
import sys
import urllib.request
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote, urlparse

from logger_setup import get_logger
from mail_settings import load_mail_settings

log = get_logger("ContractReminder")

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"

_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "[::1]"}


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------

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


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ------------------------------------------------------------------
# notification senders
# ------------------------------------------------------------------

def _generate_html(account: dict, contract: dict, days_left: int) -> str:
    end_date = contract.get("end_date", "")
    dl = contract.get("download_mbps", 0)
    ul = contract.get("upload_mbps", 0)
    provider = account.get("provider", "your ISP")
    acct_name = account.get("name", "")

    urgency_colour = "#dc2626" if days_left <= 7 else "#f59e0b"

    return f"""<html>
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f9fafb; color: #1f2937; padding: 20px; margin: 0; }}
  .container {{ max-width: 600px; margin: auto; background: #fff; padding: 28px; border-radius: 12px; border: 2px solid {urgency_colour}; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }}
  .header {{ font-size: 24px; font-weight: 700; margin-bottom: 16px; color: {urgency_colour}; }}
  .icon {{ font-size: 48px; margin-bottom: 10px; }}
  .detail {{ font-size: 14px; color: #374151; margin: 6px 0; }}
  .highlight {{ font-size: 32px; font-weight: 700; color: {urgency_colour}; }}
  .footer {{ margin-top: 24px; padding-top: 16px; border-top: 1px solid #e5e7eb; font-size: 12px; color: #9ca3af; text-align: center; }}
</style>
</head>
<body>
<div class="container">
  <div class="icon">📋</div>
  <div class="header">Contract Expiry Reminder</div>
  <p class="detail">Your broadband contract with <strong>{provider}</strong> is ending soon.</p>
  <p class="highlight">{days_left} day{"s" if days_left != 1 else ""} remaining</p>
  <p class="detail"><strong>End date:</strong> {end_date}</p>
  <p class="detail"><strong>Contracted speeds:</strong> {dl} / {ul} Mbps</p>
  <div class="footer">
    <p><strong>Account:</strong> {acct_name}</p>
    <p>This is an automated reminder from SpeedPulse.</p>
  </div>
</div>
</body>
</html>
"""


def _send_email(config: dict, html_body: str, days_left: int) -> bool:
    try:
        mail = load_mail_settings(config)
    except Exception as exc:
        log.error("Failed to load mail settings: %s", exc)
        return False

    msg = MIMEMultipart()
    msg["From"] = mail.from_addr
    msg["To"] = mail.to_addr
    msg["Subject"] = f"📋 Contract Expiry Reminder — {days_left} day{'s' if days_left != 1 else ''} left"
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if mail.smtp_port == 465:
            server = smtplib.SMTP_SSL(mail.smtp_server, mail.smtp_port, timeout=60)
        else:
            server = smtplib.SMTP(mail.smtp_server, mail.smtp_port, timeout=60)
            server.starttls()
        server.login(mail.smtp_username, mail.smtp_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as exc:
        log.error("SMTP error: %s", exc)
        return False


def _send_webhook(config: dict, days_left: int, contract: dict) -> bool:
    notifications = config.get("notifications", {})
    if not notifications.get("webhook_enabled", False):
        return False
    url = str(notifications.get("webhook_url", "")).strip()
    if not url:
        return False

    payload = {
        "title": "Contract Expiry Reminder",
        "days_remaining": days_left,
        "end_date": contract.get("end_date", ""),
        "download_mbps": contract.get("download_mbps", 0),
        "upload_mbps": contract.get("upload_mbps", 0),
    }
    try:
        _validate_outbound_url(url)
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "speedpulse/1.0"},
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            if int(resp.status) >= 300:
                log.error("Webhook returned HTTP %s", resp.status)
                return False
        log.info("Webhook contract reminder sent")
        return True
    except Exception as exc:
        log.error("Webhook send failed: %s", exc)
        return False


def _send_ntfy(config: dict, days_left: int, contract: dict) -> bool:
    notifications = config.get("notifications", {})
    if not notifications.get("ntfy_enabled", False):
        return False
    topic = str(notifications.get("ntfy_topic", "")).strip()
    if not topic:
        return False

    base_url = str(notifications.get("ntfy_server", "https://ntfy.sh")).strip() or "https://ntfy.sh"
    url = f"{base_url.rstrip('/')}/{quote(topic, safe='')}"
    try:
        _validate_outbound_url(url)
    except ValueError as exc:
        log.error("Invalid ntfy URL: %s", exc)
        return False

    message = (
        f"Contract expiry reminder\n"
        f"Days remaining: {days_left}\n"
        f"End date: {contract.get('end_date', '')}\n"
        f"Speeds: {contract.get('download_mbps', 0)} / {contract.get('upload_mbps', 0)} Mbps"
    )
    try:
        req = urllib.request.Request(
            url,
            data=message.encode("utf-8"),
            method="POST",
            headers={
                "Title": "Contract Expiry Reminder",
                "Priority": "4",
                "Tags": "calendar,warning",
                "User-Agent": "speedpulse/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            if int(resp.status) >= 300:
                log.error("ntfy returned HTTP %s", resp.status)
                return False
        log.info("ntfy contract reminder sent")
        return True
    except Exception as exc:
        log.error("ntfy send failed: %s", exc)
        return False


# ------------------------------------------------------------------
# main
# ------------------------------------------------------------------

def main() -> None:
    config = load_config()
    contract_cfg = config.get("contract", {})
    current = contract_cfg.get("current", {})

    end_date_str = current.get("end_date", "").strip()
    if not end_date_str:
        log.info("No contract end date configured — nothing to send")
        return

    try:
        end_date = date.fromisoformat(end_date_str)
    except ValueError:
        log.error("Invalid end_date format: %s", end_date_str)
        return

    days_left = (end_date - date.today()).days
    reminder_days = int(current.get("reminder_days", 31))

    log.info("Contract ends in %d days — sending %d-day reminder", days_left, reminder_days)

    account = config.get("account", {})
    html = _generate_html(account, current, days_left)

    email_ok = _send_email(config, html, days_left)
    webhook_ok = _send_webhook(config, days_left, current)
    ntfy_ok = _send_ntfy(config, days_left, current)

    if email_ok or webhook_ok or ntfy_ok:
        channels = [c for c, ok in [("email", email_ok), ("webhook", webhook_ok), ("ntfy", ntfy_ok)] if ok]
        log.info("Contract reminder sent via: %s", ", ".join(channels))
    else:
        log.error("Failed to send contract reminder via any channel")
        sys.exit(1)


if __name__ == "__main__":
    main()
