#!/usr/bin/env python3
"""
SendAlert.py - Real-time alerting for speed test violations
Sends email alerts when speed test results fall below thresholds
Uses SMTP credentials from environment variables (.env in Docker)
"""

import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from config_loader import load_json_config
from logger_setup import get_logger
from mail_settings import load_mail_settings
from push_notifications import send_ntfy_event, send_webhook_event

log = get_logger("SendAlert")


def load_config():
    """Load configuration from config.json"""
    return load_json_config(__file__)


def check_cooldown(cooldown_file, cooldown_minutes):
    """Check if alert cooldown period has passed"""
    # Resolve cooldown file relative to script directory
    script_dir = Path(__file__).parent
    cooldown_path = script_dir / cooldown_file if not Path(cooldown_file).is_absolute() else Path(cooldown_file)

    if cooldown_path.exists():
        try:
            with open(cooldown_path, 'r', encoding='utf-8') as f:
                last_alert_str = f.read().strip()
            last_alert = datetime.strptime(last_alert_str, "%Y-%m-%d %H:%M:%S")
            minutes_since = (datetime.now() - last_alert).total_seconds() / 60

            if minutes_since < cooldown_minutes:
                log.info("Alert cooldown active. Last alert was %.1f minutes ago.", minutes_since)
                return False
        except Exception as e:
            log.warning("Error reading cooldown file: %s", e)

    return True


def generate_html_alert(config, violations, download, upload, ping, packet_loss):
    """Generate HTML email body for alert"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Build violation list
    violation_list_html = ""
    for v in violations:
        violation_list_html += f"<li style='color:#dc2626; font-weight:600;'>{v}</li>\n"

    # Determine metric colors
    dl_class = "metric-bad" if download < config['thresholds']['download_mbps'] else "metric-good"
    ul_class = "metric-bad" if upload < config['thresholds']['upload_mbps'] else "metric-good"
    ping_class = "metric-bad" if ping > config['thresholds']['ping_ms'] else "metric-good"
    pl_class = "metric-bad" if packet_loss > config['thresholds']['packet_loss_percent'] else "metric-good"

    cooldown_minutes = config['email']['alert_cooldown_minutes']

    html = f"""<html>
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f9fafb; color: #1f2937; padding: 20px; margin: 0; }}
  .container {{ max-width: 600px; margin: auto; background: #fff; padding: 28px; border-radius: 12px; border: 2px solid #fbbf24; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }}
  .header {{ font-size: 24px; font-weight: 700; margin-bottom: 16px; color: #dc2626; }}
  .alert-icon {{ font-size: 48px; margin-bottom: 10px; }}
  .timestamp {{ font-size: 13px; color: #6b7280; margin-bottom: 20px; }}
  .violations {{ background: #fef2f2; border-left: 4px solid #dc2626; padding: 16px; margin: 20px 0; border-radius: 6px; }}
  .violations h3 {{ margin-top: 0; color: #991b1b; font-size: 16px; }}
  .violations ul {{ margin: 10px 0; padding-left: 20px; }}
  .violations li {{ margin: 8px 0; }}
  .metrics {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 20px; }}
  .metric-card {{ background: #f3f4f6; padding: 14px; border-radius: 8px; text-align: center; }}
  .metric-label {{ font-size: 12px; color: #6b7280; text-transform: uppercase; font-weight: 600; }}
  .metric-value {{ font-size: 24px; font-weight: 700; margin-top: 6px; }}
  .metric-bad {{ color: #dc2626; }}
  .metric-good {{ color: #16a34a; }}
  .footer {{ margin-top: 24px; padding-top: 16px; border-top: 1px solid #e5e7eb; font-size: 12px; color: #9ca3af; text-align: center; }}
  .action {{ background: #3b82f6; color: white; padding: 12px 24px; border-radius: 6px; text-decoration: none; display: inline-block; margin-top: 16px; font-weight: 600; }}
</style>
</head>
<body>
<div class="container">
  <div class="alert-icon">⚠️</div>
  <div class="header">SpeedPulse Alert</div>
  <div class="timestamp">Detected at: {timestamp}</div>
  
  <div class="violations">
    <h3>Threshold Violations Detected:</h3>
    <ul>
      {violation_list_html}
    </ul>
  </div>
  
  <div class="metrics">
    <div class="metric-card">
      <div class="metric-label">Download</div>
      <div class="metric-value {dl_class}">{download} Mbps</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Upload</div>
      <div class="metric-value {ul_class}">{upload} Mbps</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Ping</div>
      <div class="metric-value {ping_class}">{round(ping, 1)} ms</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Packet Loss</div>
      <div class="metric-value {pl_class}">{round(packet_loss, 1)}%</div>
    </div>
  </div>
  
  <div class="footer">
    <p><strong>Account:</strong> {config['account']['name']} ({config['account']['number']})</p>
    <p>This is an automated alert from SpeedPulse.</p>
    <p>Next alert will be sent after {cooldown_minutes} minutes cooldown period.</p>
  </div>
</div>
</body>
</html>
"""
    return html


def send_alert_email(config, subject, body):
    """Send alert email via SMTP"""

    try:
        mail = load_mail_settings(config)
    except Exception as e:
        log.error("Failed to load mail settings: %s", e)
        return False

    # Create message
    msg = MIMEMultipart()
    msg['From'] = mail.from_addr
    msg['To'] = mail.to_addr
    msg['Subject'] = subject

    # Attach HTML body
    msg.attach(MIMEText(body, 'html', 'utf-8'))

    # Send email
    try:
        # Use SMTP_SSL for port 465 or SMTP with STARTTLS for port 587
        if mail.smtp_port == 465:
            server = smtplib.SMTP_SSL(mail.smtp_server, mail.smtp_port, timeout=60)
        else:
            server = smtplib.SMTP(mail.smtp_server, mail.smtp_port, timeout=60)
            server.starttls()

        server.login(mail.smtp_username, mail.smtp_password)
        server.send_message(msg)
        server.quit()

        return True

    except smtplib.SMTPException as e:
        log.error("SMTP Error: %s", e)
        return False
    except Exception as e:
        log.error("Failed to send email: %s", e)
        return False


def send_webhook_alert(config, violations, download, upload, ping, packet_loss):
    """Send webhook alert when channel and event preference are enabled."""
    return send_webhook_event(
        config,
        "alert",
        "SpeedPulse Alert",
        f"Violations: {', '.join(violations)}",
        payload_extra={
            "violations": violations,
            "metrics": {
                "download_mbps": download,
                "upload_mbps": upload,
                "ping_ms": ping,
                "packet_loss_percent": packet_loss,
            },
        },
        logger=log,
    )


def send_ntfy_alert(config, violations, download, upload, ping, packet_loss):
    """Send ntfy alert when channel and event preference are enabled."""
    message = (
        "SpeedPulse alert\n"
        f"Download: {download} Mbps\n"
        f"Upload: {upload} Mbps\n"
        f"Ping: {ping} ms\n"
        f"Packet loss: {packet_loss}%\n"
        f"Violations: {', '.join(violations)}"
    )
    return send_ntfy_event(
        config,
        "alert",
        "Speed Alert",
        message,
        priority="4",
        tags="warning,satellite",
        logger=log,
    )


def main():
    """Main execution function"""

    # Parse command line arguments
    if len(sys.argv) < 6:
        log.error("Usage: python SendAlert.py <download> <upload> <ping> <packet_loss> <violation1> [violation2] ...")
        sys.exit(1)

    try:
        download = float(sys.argv[1])
        upload = float(sys.argv[2])
        ping = float(sys.argv[3])
        packet_loss = float(sys.argv[4])
        violations = sys.argv[5:]
    except ValueError:
        log.error("Invalid numeric values for download, upload, ping, or packet loss")
        sys.exit(1)

    # Load configuration
    config = load_config()
    script_dir = Path(__file__).parent

    # Check cooldown
    cooldown_file = script_dir / "last_alert.txt"
    cooldown_minutes = config['email']['alert_cooldown_minutes']

    if not check_cooldown(cooldown_file, cooldown_minutes):
        sys.exit(0)

    # Generate alert email
    subject = "⚠️ SpeedPulse Alert - Threshold Violations Detected"
    body = generate_html_alert(config, violations, download, upload, ping, packet_loss)

    email_success = send_alert_email(config, subject, body)
    webhook_success = send_webhook_alert(config, violations, download, upload, ping, packet_loss)
    ntfy_success = send_ntfy_alert(config, violations, download, upload, ping, packet_loss)
    success = email_success or webhook_success or ntfy_success

    if success:
        # Update last alert time
        with open(cooldown_file, 'w', encoding='utf-8') as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        sent_channels = []
        if email_success:
            sent_channels.append("email")
        if webhook_success:
            sent_channels.append("webhook")
        if ntfy_success:
            sent_channels.append("ntfy")

        # Log to notification history
        try:
            from state_store import log_notification
            summary = f"Violations: {', '.join(violations)} | DL {download} UL {upload} Ping {ping}"
            for ch in sent_channels:
                log_notification(ch, "alert", summary)
        except Exception:
            pass

        log.info("Alert notification sent via: %s", ", ".join(sent_channels))
        sys.exit(0)
    else:
        # Log error
        script_dir = Path(__file__).parent
        error_log = script_dir / config['paths']['error_log']
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(error_log, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] Failed to send alert notifications (email/webhook/ntfy)\n")

        sys.exit(1)


if __name__ == "__main__":
    main()
