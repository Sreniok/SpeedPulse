#!/usr/bin/env python3
"""
health_check.py - System Health Monitoring
Performs daily health checks and sends email alerts when issues are detected
"""

import os
import smtplib
import sys
from datetime import datetime, time, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config_loader import load_json_config_or_exit, resolve_runtime_path
from mail_settings import load_mail_settings
from measurement_repository import load_measurement_entries
from measurement_store import record_notification_event
from push_notifications import send_ntfy_event, send_webhook_event

_SCHEDULE_LOOKBACK_DAYS = 400
_DEFAULT_TEST_TIMES = ["08:00", "16:00", "22:00"]
_WEEKDAY_MAP = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def load_config():
    """Load configuration from config.json"""
    return load_json_config_or_exit(
        __file__,
        missing_message="❌ Configuration file not found: config.json",
        on_missing=print,
        exit_code=1,
    )


def check_disk_space(path, min_gb=1.0):
    """Check available disk space at given path"""
    try:
        stat = os.statvfs(path)
        available_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
        total_gb = (stat.f_blocks * stat.f_frsize) / (1024**3)
        used_percent = ((total_gb - available_gb) / total_gb) * 100

        return {
            'available_gb': round(available_gb, 2),
            'total_gb': round(total_gb, 2),
            'used_percent': round(used_percent, 1),
            'healthy': available_gb >= min_gb,
            'status': 'OK' if available_gb >= min_gb else 'LOW'
        }
    except Exception as e:
        return {
            'available_gb': 0,
            'total_gb': 0,
            'used_percent': 0,
            'healthy': False,
            'status': 'ERROR',
            'error': str(e)
        }


def check_log_files(config):
    """Check log file sizes and rotation status"""
    issues = []
    log_dir = resolve_runtime_path(__file__, config['paths']['log_directory'])

    if not log_dir.exists():
        return {
            'healthy': False,
            'issues': ['Log directory does not exist']
        }

    # Check total log directory size
    total_size_mb = 0.0
    log_files = list(log_dir.glob('speed_log_week_*.txt'))

    for log_file in log_files:
        size_mb = log_file.stat().st_size / (1024 * 1024)
        total_size_mb += size_mb

        # Warn if individual log file is very large (>10MB)
        if size_mb > 10:
            issues.append(f"{log_file.name} is large ({size_mb:.1f} MB)")

    # Check number of log files
    if len(log_files) > 60:  # More than a year of logs
        issues.append(f"Too many log files ({len(log_files)}). Consider archiving old logs.")

    return {
        'healthy': len(issues) == 0,
        'total_size_mb': round(total_size_mb, 2),
        'file_count': len(log_files),
        'issues': issues
    }


def _parse_test_times(config):
    scheduling = config.get("scheduling", {})
    raw_times = scheduling.get("test_times", _DEFAULT_TEST_TIMES)
    if not isinstance(raw_times, list):
        raw_times = _DEFAULT_TEST_TIMES

    parsed_times = []
    for raw_value in raw_times:
        value = str(raw_value or "").strip()
        if not value or ":" not in value:
            continue
        hour_str, minute_str = value.split(":", 1)
        try:
            parsed_times.append(time(int(hour_str), int(minute_str)))
        except ValueError:
            continue

    return sorted(parsed_times)


def _parse_scan_frequency(config):
    frequency = str(
        config.get("scheduling", {}).get("scan_frequency", "daily") or "daily"
    ).strip().lower()
    return frequency if frequency in {"daily", "weekly", "monthly", "custom"} else "daily"


def _normalize_custom_scan_days(values):
    if not isinstance(values, list):
        return [1]

    normalized = sorted(
        {
            day
            for day in (
                int(value) for value in values if str(value).strip().isdigit()
            )
            if 1 <= day <= 31
        }
    )
    return normalized or [1]


def _speedtest_completion_grace_period(config):
    speedtest_cfg = config.get("speedtest", {})

    try:
        max_retries = max(1, int(speedtest_cfg.get("max_retries", 3) or 3))
    except (TypeError, ValueError):
        max_retries = 3

    try:
        timeout_seconds = max(0, int(speedtest_cfg.get("timeout_seconds", 120) or 120))
    except (TypeError, ValueError):
        timeout_seconds = 120

    try:
        retry_delay_seconds = max(0, int(speedtest_cfg.get("retry_delay_seconds", 30) or 30))
    except (TypeError, ValueError):
        retry_delay_seconds = 30

    estimated_runtime = (max_retries * timeout_seconds) + (max(0, max_retries - 1) * retry_delay_seconds)
    return timedelta(seconds=max(estimated_runtime, 600))


def _is_scheduled_scan_day(config, target_date):
    scheduling = config.get("scheduling", {})
    frequency = _parse_scan_frequency(config)

    if frequency == "weekly":
        weekday_name = str(scheduling.get("scan_weekly_day", "Monday") or "Monday").strip().lower()[:3]
        return target_date.weekday() == _WEEKDAY_MAP.get(weekday_name, 0)
    if frequency == "monthly":
        try:
            day_of_month = int(scheduling.get("scan_monthly_day", 1) or 1)
        except (TypeError, ValueError):
            day_of_month = 1
        day_of_month = max(1, min(31, day_of_month))
        return target_date.day == day_of_month
    if frequency == "custom":
        return target_date.day in _normalize_custom_scan_days(scheduling.get("scan_custom_days", []))
    return True


def _latest_due_scheduled_run(config, now=None):
    scheduling = config.get("scheduling", {})
    if not bool(scheduling.get("scan_enabled", True)):
        return None

    test_times = _parse_test_times(config)
    if not test_times:
        return None

    current_time = (now or datetime.now()).replace(second=0, microsecond=0)
    due_cutoff = current_time - _speedtest_completion_grace_period(config)

    for day_offset in range(_SCHEDULE_LOOKBACK_DAYS + 1):
        candidate_date = due_cutoff.date() - timedelta(days=day_offset)
        if not _is_scheduled_scan_day(config, candidate_date):
            continue

        for test_time in reversed(test_times):
            candidate_run = datetime.combine(candidate_date, test_time)
            if candidate_run <= due_cutoff:
                return candidate_run

    return None


def check_last_speedtest(config):
    """Check whether the latest due scheduled speed test completed."""
    try:
        entries = []
        for entry in load_measurement_entries(config):
            timestamp = entry.get("timestamp")
            if timestamp is None or not hasattr(timestamp, "strftime"):
                continue
            if str(entry.get("source", "scheduled")).strip().lower() != "scheduled":
                continue
            entries.append(entry)
        last_test = entries[-1]["timestamp"] if entries else None
        hours_ago = None
        if last_test is not None:
            hours_ago = (datetime.now() - last_test).total_seconds() / 3600

        expected_run = _latest_due_scheduled_run(config)
        if expected_run is None:
            return {
                'healthy': True,
                'last_test': last_test.strftime("%Y-%m-%d %H:%M:%S") if last_test else None,
                'hours_ago': round(hours_ago, 1) if hours_ago is not None else None,
                'issue': None
            }

        healthy = last_test is not None and last_test >= expected_run

        return {
            'healthy': healthy,
            'last_test': last_test.strftime("%Y-%m-%d %H:%M:%S") if last_test else None,
            'hours_ago': round(hours_ago, 1) if hours_ago is not None else None,
            'issue': (
                f"Scheduled speed test due at {expected_run.strftime('%Y-%m-%d %H:%M')} has not completed"
                if not healthy else None
            )
        }

    except Exception as e:
        return {
            'healthy': False,
            'last_test': None,
            'hours_ago': None,
            'issue': f'Error reading log file: {e}'
        }


def check_error_log(config):
    """Check error log for recent issues"""
    error_log = resolve_runtime_path(__file__, config['paths']['error_log'])

    if not error_log.exists():
        return {
            'healthy': True,
            'recent_errors': 0,
            'issues': []
        }

    try:
        with open(error_log, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Check for errors in last 24 hours
        now = datetime.now()
        recent_errors = []

        for line in reversed(lines[-100:]):  # Check last 100 lines
            if not line.strip():
                continue

            try:
                # Extract timestamp [YYYY-MM-DD HH:MM:SS]
                if line.startswith('['):
                    timestamp_str = line[1:20]
                    error_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                    hours_ago = (now - error_time).total_seconds() / 3600

                    if hours_ago < 24:
                        recent_errors.append(line.strip())
            except (ValueError, IndexError):
                continue

        # More than 5 errors in 24 hours is concerning
        healthy = len(recent_errors) < 5

        return {
            'healthy': healthy,
            'recent_errors': len(recent_errors),
            'issues': recent_errors[:5] if not healthy else []  # Show max 5
        }

    except Exception as e:
        return {
            'healthy': False,
            'recent_errors': 0,
            'issues': [f'Error reading error log: {e}']
        }


def check_config_integrity(config):
    """Verify config.json has all required fields"""
    required_sections = ['account', 'paths', 'thresholds', 'email', 'speedtest']
    required_paths = ['log_directory', 'images_directory', 'error_log']

    issues = []

    # Check required sections
    for section in required_sections:
        if section not in config:
            issues.append(f"Missing config section: {section}")

    # Check required paths exist
    if 'paths' in config:
        for path_key in required_paths:
            if path_key not in config['paths']:
                issues.append(f"Missing path config: {path_key}")
            else:
                path = resolve_runtime_path(__file__, config['paths'][path_key])
                if path_key.endswith('_directory'):
                    if not path.exists():
                        issues.append(f"Directory does not exist: {path}")

    return {
        'healthy': len(issues) == 0,
        'issues': issues
    }


def check_credentials(config):
    """Check if .env mail settings are accessible."""
    try:
        load_mail_settings(config)
        return {
            'healthy': True,
            'issue': None
        }
    except Exception as e:
        return {
            'healthy': False,
            'issue': f'Error accessing credentials: {e}'
        }


def generate_health_report_html(config, health_data):
    """Generate HTML email with health check results"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Determine overall health
    all_healthy = all([
        health_data['disk']['healthy'],
        health_data['logs']['healthy'],
        health_data['speedtest']['healthy'],
        health_data['errors']['healthy'],
        health_data['config']['healthy'],
        health_data['credentials']['healthy']
    ])

    status_color = "#16a34a" if all_healthy else "#dc2626"
    status_icon = "✅" if all_healthy else "⚠️"

    # Build issues list
    issues_html = ""

    if not health_data['disk']['healthy']:
        issues_html += f"<li><strong>Disk Space:</strong> Only {health_data['disk']['available_gb']} GB available ({health_data['disk']['used_percent']}% used)</li>\n"

    if not health_data['logs']['healthy']:
        for issue in health_data['logs']['issues']:
            issues_html += f"<li><strong>Log Files:</strong> {issue}</li>\n"

    if not health_data['speedtest']['healthy']:
        issues_html += f"<li><strong>Speed Tests:</strong> {health_data['speedtest']['issue']}</li>\n"

    if not health_data['errors']['healthy']:
        issues_html += f"<li><strong>Recent Errors:</strong> {health_data['errors']['recent_errors']} errors in last 24 hours</li>\n"
        for error in health_data['errors']['issues'][:3]:  # Show max 3
            issues_html += f"<li class='sub-issue'>{error[:150]}</li>\n"

    if not health_data['config']['healthy']:
        for issue in health_data['config']['issues']:
            issues_html += f"<li><strong>Configuration:</strong> {issue}</li>\n"

    if not health_data['credentials']['healthy']:
        issues_html += f"<li><strong>Credentials:</strong> {health_data['credentials']['issue']}</li>\n"

    html = f"""<html>
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f9fafb; color: #1f2937; padding: 20px; margin: 0; }}
  .container {{ max-width: 650px; margin: auto; background: #fff; padding: 28px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }}
  .header {{ font-size: 24px; font-weight: 700; margin-bottom: 10px; color: {status_color}; }}
  .status-icon {{ font-size: 48px; margin-bottom: 10px; }}
  .timestamp {{ font-size: 13px; color: #6b7280; margin-bottom: 20px; }}
  .section {{ background: #f9fafb; padding: 16px; border-radius: 8px; margin: 16px 0; border-left: 4px solid #3b82f6; }}
  .section-title {{ font-weight: 700; font-size: 14px; color: #374151; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .metric {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #e5e7eb; }}
  .metric:last-child {{ border-bottom: none; }}
  .metric-label {{ color: #6b7280; font-size: 14px; }}
  .metric-value {{ font-weight: 600; font-size: 14px; }}
  .status-ok {{ color: #16a34a; }}
  .status-error {{ color: #dc2626; }}
  .issues {{ background: #fef2f2; border-left: 4px solid #dc2626; padding: 16px; margin: 20px 0; border-radius: 6px; }}
  .issues h3 {{ margin-top: 0; color: #991b1b; font-size: 16px; }}
  .issues ul {{ margin: 10px 0; padding-left: 20px; }}
  .issues li {{ margin: 8px 0; line-height: 1.5; }}
  .sub-issue {{ font-size: 12px; color: #6b7280; font-family: monospace; }}
  .footer {{ margin-top: 24px; padding-top: 16px; border-top: 1px solid #e5e7eb; font-size: 12px; color: #9ca3af; text-align: center; }}
</style>
</head>
<body>
<div class="container">
  <div class="status-icon">{status_icon}</div>
  <div class="header">System Health Check</div>
  <div class="timestamp">{timestamp}</div>
"""

    if not all_healthy:
        html += f"""
  <div class="issues">
    <h3>Issues Detected:</h3>
    <ul>
      {issues_html}
    </ul>
  </div>
"""

    html += f"""
  <div class="section">
    <div class="section-title">💾 Disk Space</div>
    <div class="metric">
      <span class="metric-label">Available</span>
      <span class="metric-value {'status-ok' if health_data['disk']['healthy'] else 'status-error'}">{health_data['disk']['available_gb']} GB</span>
    </div>
    <div class="metric">
      <span class="metric-label">Total</span>
      <span class="metric-value">{health_data['disk']['total_gb']} GB</span>
    </div>
    <div class="metric">
      <span class="metric-label">Used</span>
      <span class="metric-value">{health_data['disk']['used_percent']}%</span>
    </div>
  </div>
  
  <div class="section">
    <div class="section-title">📝 Log Files</div>
    <div class="metric">
      <span class="metric-label">Total Size</span>
      <span class="metric-value">{health_data['logs']['total_size_mb']} MB</span>
    </div>
    <div class="metric">
      <span class="metric-label">File Count</span>
      <span class="metric-value">{health_data['logs']['file_count']} files</span>
    </div>
    <div class="metric">
      <span class="metric-label">Status</span>
      <span class="metric-value {'status-ok' if health_data['logs']['healthy'] else 'status-error'}">{'OK' if health_data['logs']['healthy'] else 'Issues'}</span>
    </div>
  </div>
  
  <div class="section">
    <div class="section-title">🚀 Speed Tests</div>
    <div class="metric">
      <span class="metric-label">Last Test</span>
      <span class="metric-value">{health_data['speedtest']['last_test'] if health_data['speedtest']['last_test'] else 'N/A'}</span>
    </div>
    <div class="metric">
      <span class="metric-label">Hours Ago</span>
      <span class="metric-value {'status-ok' if health_data['speedtest']['healthy'] else 'status-error'}">{health_data['speedtest']['hours_ago'] if health_data['speedtest']['hours_ago'] else 'N/A'}</span>
    </div>
  </div>
  
  <div class="section">
    <div class="section-title">⚠️ Error Log</div>
    <div class="metric">
      <span class="metric-label">Errors (24h)</span>
      <span class="metric-value {'status-ok' if health_data['errors']['healthy'] else 'status-error'}">{health_data['errors']['recent_errors']}</span>
    </div>
  </div>
  
  <div class="section">
    <div class="section-title">⚙️ Configuration</div>
    <div class="metric">
      <span class="metric-label">Config Status</span>
      <span class="metric-value {'status-ok' if health_data['config']['healthy'] else 'status-error'}">{'Valid' if health_data['config']['healthy'] else 'Issues'}</span>
    </div>
    <div class="metric">
      <span class="metric-label">Credentials</span>
      <span class="metric-value {'status-ok' if health_data['credentials']['healthy'] else 'status-error'}">{'OK' if health_data['credentials']['healthy'] else 'Error'}</span>
    </div>
  </div>
  
  <div class="footer">
    <p>SpeedPulse - Automated Health Check</p>
    <p>Account: {config['account']['name']} ({config['account']['number']})</p>
  </div>
</div>
</body>
</html>
"""

    return html


def send_health_alert(config, health_data):
    """Send health check email alert"""
    try:
        mail = load_mail_settings(config)

        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = "⚠️ System Health Check - Issues Detected" if not all([
            health_data['disk']['healthy'],
            health_data['logs']['healthy'],
            health_data['speedtest']['healthy'],
            health_data['errors']['healthy'],
            health_data['config']['healthy'],
            health_data['credentials']['healthy']
        ]) else "✅ System Health Check - All OK"
        msg['From'] = mail.from_addr
        msg['To'] = mail.to_addr

        # Generate HTML body
        html_body = generate_health_report_html(config, health_data)
        msg.attach(MIMEText(html_body, 'html'))

        # Send email
        server: smtplib.SMTP | smtplib.SMTP_SSL
        if mail.smtp_port == 465:
            server = smtplib.SMTP_SSL(mail.smtp_server, mail.smtp_port, timeout=60)
        else:
            server = smtplib.SMTP(mail.smtp_server, mail.smtp_port, timeout=60)
            server.starttls()

        with server:
            server.login(mail.smtp_username, mail.smtp_password)
            server.send_message(msg)

        print("✅ Health check alert sent successfully")
        try:
            from state_store import log_notification
            issues = sum(1 for k in ("disk","logs","speedtest","errors","config","credentials") if not health_data.get(k, {}).get("healthy", True))
            summary = f"{issues} issue(s) found" if issues else "All checks passed"
            log_notification("email", "health_check", summary)
            record_notification_event("email", "health_check", summary)

            webhook_success = send_webhook_event(
                config,
                "health_check",
                "SpeedPulse health check",
                summary,
                payload_extra={"issues": issues},
            )
            ntfy_success = send_ntfy_event(
                config,
                "health_check",
                "Health check",
                summary,
                priority="3" if issues == 0 else "4",
                tags="wrench,monitoring" if issues else "monitoring",
            )
            if webhook_success:
                log_notification("webhook", "health_check", summary)
                record_notification_event("webhook", "health_check", summary)
            if ntfy_success:
                log_notification("ntfy", "health_check", summary)
                record_notification_event("ntfy", "health_check", summary)
        except Exception:
            pass
        return True

    except Exception as e:
        print(f"❌ Failed to send health alert: {e}")
        return False


def main():
    """Main health check function"""
    print("🔍 Running system health check...")

    # Load configuration
    config = load_config()

    log_directory = str(resolve_runtime_path(__file__, config['paths']['log_directory']))

    # Perform health checks
    health_data = {
        'disk': check_disk_space(log_directory),
        'logs': check_log_files(config),
        'speedtest': check_last_speedtest(config),
        'errors': check_error_log(config),
        'config': check_config_integrity(config),
        'credentials': check_credentials(config)
    }

    # Display results
    print("\n📊 Health Check Results:")
    print(f"  Disk Space: {'✅ OK' if health_data['disk']['healthy'] else '❌ LOW'} ({health_data['disk']['available_gb']} GB available)")
    print(f"  Log Files: {'✅ OK' if health_data['logs']['healthy'] else '❌ Issues'} ({health_data['logs']['file_count']} files, {health_data['logs']['total_size_mb']} MB)")
    print(f"  Speed Tests: {'✅ OK' if health_data['speedtest']['healthy'] else '❌ Issue'} (Last: {health_data['speedtest']['hours_ago']}h ago)")
    print(f"  Error Log: {'✅ OK' if health_data['errors']['healthy'] else '⚠️  Issues'} ({health_data['errors']['recent_errors']} errors in 24h)")
    print(f"  Config: {'✅ OK' if health_data['config']['healthy'] else '❌ Issues'}")
    print(f"  Credentials: {'✅ OK' if health_data['credentials']['healthy'] else '❌ Error'}")

    # Determine if we need to send alert
    all_healthy = all([
        health_data['disk']['healthy'],
        health_data['logs']['healthy'],
        health_data['speedtest']['healthy'],
        health_data['errors']['healthy'],
        health_data['config']['healthy'],
        health_data['credentials']['healthy']
    ])

    if not all_healthy:
        print("\n⚠️  Issues detected - sending alert email...")
        send_health_alert(config, health_data)
    else:
        print("\n✅ All systems healthy - no alert needed")

    return 0 if all_healthy else 1


if __name__ == "__main__":
    sys.exit(main())
