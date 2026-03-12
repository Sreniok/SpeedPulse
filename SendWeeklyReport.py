#!/usr/bin/env python3
"""
Enhanced SendWeeklyReport.py - Weekly Speed Test Report with Historical Comparison
Sends weekly speed test reports via email with charts and statistics
Uses SMTP credentials from environment variables (.env in Docker)
"""

import json
import os
import smtplib
import subprocess
import sys
from datetime import datetime, timedelta
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from log_parser import parse_weekly_log_file
from logger_setup import get_logger
from mail_settings import load_mail_settings

log = get_logger("SendWeeklyReport")


def get_iso_week(date=None):
    """Get ISO week number"""
    if date is None:
        date = datetime.now() - timedelta(days=1)
    return date.isocalendar()[1]


def load_config():
    """Load configuration from config.json"""
    config_path = Path(__file__).parent / "config.json"
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def parse_log_file(log_file):
    """Extract per-metric lists from a weekly log file via the shared parser."""
    entries = parse_weekly_log_file(Path(log_file))
    downloads = [e["download_mbps"] for e in entries]
    uploads = [e["upload_mbps"] for e in entries]
    pings = [e["ping_ms"] for e in entries]
    jitters = [e["jitter_ms"] for e in entries]
    packet_losses = [e["packet_loss_percent"] for e in entries]
    return downloads, uploads, pings, jitters, packet_losses


def parse_log_for_table(log_file):
    """Return per-test dicts with display-ready string values for the HTML table."""
    entries = parse_weekly_log_file(Path(log_file))
    results = []
    for e in entries:
        results.append({
            "Date": e["timestamp"].strftime("%d-%m-%Y"),
            "Time": e["timestamp"].strftime("%H:%M"),
            "Server": e.get("server", "Unknown"),
            "ISP": e.get("isp", "Unknown"),
            "Ping": f"{e['ping_ms']:.1f} ms",
            "Jitter": f"{e['jitter_ms']:.1f} ms",
            "PacketLoss": f"{e['packet_loss_percent']:.2f}%",
            "Download": f"{e['download_mbps']:.1f} Mbps",
            "Upload": f"{e['upload_mbps']:.1f} Mbps",
        })
    return results


def calculate_stats(values):
    """Calculate min, max, avg for a list of values"""
    if not values:
        return 0, 0, 0
    return min(values), max(values), sum(values) / len(values)


def generate_html_report(config, week_num, previous_week_num, downloads, uploads, pings, jitters,
                         packet_losses, prev_dl_avg, prev_ul_avg, test_results, chart_base64):
    """Generate HTML email body"""

    # Current week stats
    dl_min, dl_max, dl_avg = calculate_stats(downloads)
    ul_min, ul_max, ul_avg = calculate_stats(uploads)
    ping_avg = sum(pings) / len(pings) if pings else 0
    jitter_avg = sum(jitters) / len(jitters) if jitters else 0
    packet_loss_avg = sum(packet_losses) / len(packet_losses) if packet_losses else 0

    # Calculate reliability score
    threshold = config['thresholds']['download_mbps']
    tests_above_threshold = sum(1 for dl in downloads if dl >= threshold)
    reliability_score = round((tests_above_threshold / len(downloads)) * 100, 1) if downloads else 0

    # ISP Grade
    if reliability_score >= 95:
        isp_grade = "A"
        grade_color = "#22c55e"
    elif reliability_score >= 85:
        isp_grade = "B"
        grade_color = "#22c55e"
    elif reliability_score >= 75:
        isp_grade = "C"
        grade_color = "#eab308"
    elif reliability_score >= 65:
        isp_grade = "D"
        grade_color = "#ef4444"
    else:
        isp_grade = "F"
        grade_color = "#ef4444"

    # Trends
    trend_dl = ""
    trend_ul = ""
    comparison_html = ""

    if prev_dl_avg > 0:
        dl_change = dl_avg - prev_dl_avg
        ul_change = ul_avg - prev_ul_avg

        if dl_change > 5:
            trend_dl = f"📈 +{round(dl_change, 0)} Mbps"
        elif dl_change < -5:
            trend_dl = f"📉 {round(dl_change, 0)} Mbps"
        else:
            trend_dl = f"➡️ ~{round(dl_change, 0)} Mbps"

        if ul_change > 2:
            trend_ul = f"📈 +{round(ul_change, 0)} Mbps"
        elif ul_change < -2:
            trend_ul = f"📉 {round(ul_change, 0)} Mbps"
        else:
            trend_ul = f"➡️ ~{round(ul_change, 0)} Mbps"

        comparison_html = f"""
  <div class="comparison">
    <div class="comparison-title">📈 Week-over-Week Comparison (vs Week {previous_week_num})</div>
    <div>
      <span class="trend">Download: {trend_dl}</span> | 
      <span class="trend">Upload: {trend_ul}</span>
    </div>
  </div>
"""

    # Build table rows
    log_html_rows = ""
    for test in test_results:
        try:
            dl_val = float(''.join(c for c in test['Download'] if c.isdigit() or c == '.'))
            download_class = "speed-low" if dl_val < threshold else ("speed-high" if dl_val > 800 else "speed-normal")
        except (ValueError, KeyError):
            download_class = "speed-normal"

        try:
            ul_val = float(''.join(c for c in test['Upload'] if c.isdigit() or c == '.'))
            upload_class = "speed-low" if ul_val < config['thresholds']['upload_mbps'] else ("speed-high" if ul_val > 120 else "speed-normal")
        except (ValueError, KeyError):
            upload_class = "speed-normal"

        jitter_display = test.get('Jitter', 'N/A')
        packet_loss_display = test.get('PacketLoss', '0%')

        try:
            pl_val = float(''.join(c for c in packet_loss_display if c.isdigit() or c == '.'))
            packet_loss_class = "speed-low" if pl_val > config['thresholds']['packet_loss_percent'] else "speed-normal"
        except (ValueError, KeyError):
            packet_loss_class = "speed-normal"

        log_html_rows += f"""
    <tr>
      <td>{test['Date']}</td>
      <td>{test['Time']}</td>
      <td class="{download_class}">{test['Download']}</td>
      <td class="{upload_class}">{test['Upload']}</td>
      <td>{test['Ping']}</td>
      <td>{jitter_display}</td>
      <td class="{packet_loss_class}">{packet_loss_display}</td>
      <td class="server-info">{test['Server']}</td>
    </tr>
"""

    packet_loss_color = "speed-low" if packet_loss_avg > config['thresholds']['packet_loss_percent'] else "speed-normal"

    html = f"""<html>
<head>
<meta charset="UTF-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f4f4f4; color: #222; padding: 20px; margin: 0; }}
  .container {{ max-width: 900px; margin: auto; background: #fff; padding: 28px; border-radius: 16px; border: 1px solid #ddd; box-shadow: 0 3px 12px rgba(0,0,0,0.04); }}
  .header {{ font-size: 24px; font-weight: 700; margin-bottom: 10px; color: #1a365d; }}
  .meta {{ font-size: 14px; color: #555; margin-bottom: 20px; }}
  .summary {{ background: #f0f7ff; border: 1px solid #b3d4fc; border-radius: 8px; padding: 16px 20px; margin-bottom: 18px; font-size: 14px; }}
  .summary b {{ color: #1a4d8f; }}
  .chart {{ display: block; margin: 0 auto 20px auto; max-width: 100%; border-radius: 10px; border: 1px solid #ccc; }}
  
  .isp-scorecard {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 12px; margin: 20px 0; text-align: center; }}
  .isp-grade {{ font-size: 72px; font-weight: 900; margin: 10px 0; color: {grade_color}; text-shadow: 2px 2px 4px rgba(0,0,0,0.3); }}
  .isp-score {{ font-size: 20px; margin-top: 8px; }}
  
  .comparison {{ background: #fef3c7; border-left: 4px solid #f59e0b; padding: 14px 18px; margin: 16px 0; border-radius: 6px; }}
  .comparison-title {{ font-weight: 700; color: #92400e; margin-bottom: 8px; }}
  .trend {{ display: inline-block; margin: 0 10px; font-weight: 600; }}
  
  .metrics-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin: 20px 0; }}
  .metric-box {{ background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px; text-align: center; }}
  .metric-label {{ font-size: 11px; color: #6b7280; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px; }}
  .metric-value {{ font-size: 22px; font-weight: 700; margin-top: 6px; color: #1f2937; }}
  
  .log-section {{ margin-top: 30px; }}
  .log-title {{ font-size: 18px; font-weight: 600; margin-bottom: 15px; color: #1a365d; }}
  .log-table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12px; }}
  .log-table th {{ background: #e2e8f0; padding: 10px 6px; text-align: left; font-weight: 600; border: 1px solid #cbd5e0; color: #2d3748; font-size: 11px; }}
  .log-table td {{ padding: 8px 6px; border: 1px solid #e2e8f0; }}
  .log-table tr:nth-child(even) {{ background: #f7fafc; }}
  .log-table tr:hover {{ background: #edf2f7; }}
  
  .speed-high {{ color: #22c55e; font-weight: 600; }}
  .speed-normal {{ color: #3b82f6; }}
  .speed-low {{ color: #ef4444; font-weight: 600; }}
  .server-info {{ font-size: 10px; color: #64748b; max-width: 180px; }}
  
  .footer {{ font-size: 12px; color: #999; margin-top: 30px; text-align: center; padding-top: 20px; border-top: 1px solid #e5e7eb; }}
  
  @media (max-width: 600px) {{
    .container {{ padding: 16px; margin: 10px; }}
    .metrics-grid {{ grid-template-columns: 1fr; }}
    .log-table {{ font-size: 10px; }}
    .log-table th, .log-table td {{ padding: 6px 4px; }}
    .server-info {{ display: none; }}
  }}
</style>
</head>
<body>
<div class="container">
  <div class="header">📊 Weekly Internet Speed Report - Week {week_num}</div>
  <div class="meta">
    <strong>Account:</strong> {config['account']['name']}<br />
    <strong>Account No:</strong> {config['account']['number']}<br />
    <strong>Generated:</strong> {datetime.now().strftime("%Y-%m-%d %H:%M")}
  </div>
  
  <div class="isp-scorecard">
    <div style="font-size: 18px; font-weight: 600;">ISP Performance Grade</div>
    <div class="isp-grade">{isp_grade}</div>
    <div class="isp-score">Reliability Score: {reliability_score}%</div>
    <div style="font-size: 13px; margin-top: 8px; opacity: 0.9;">({tests_above_threshold} of {len(downloads)} tests above {threshold} Mbps)</div>
  </div>
  
  {comparison_html}
  
<div class="summary">
  <table style="width:100%; border-collapse:collapse;">
    <tr>
      <th style="text-align:left; padding-right:18px;"></th>
      <th style="text-align:left;">Min</th>
      <th style="text-align:left;">Max</th>
      <th style="text-align:left;">Avg</th>
    </tr>
    <tr>
      <td><b>📥 Download</b></td>
      <td>{dl_min:.0f} Mbps</td>
      <td>{dl_max:.0f} Mbps</td>
      <td>{dl_avg:.0f} Mbps</td>
    </tr>
    <tr>
      <td><b>📤 Upload</b></td>
      <td>{ul_min:.0f} Mbps</td>
      <td>{ul_max:.0f} Mbps</td>
      <td>{ul_avg:.0f} Mbps</td>
    </tr>
  </table>
</div>

  <div class="metrics-grid">
    <div class="metric-box">
      <div class="metric-label">Avg Ping</div>
      <div class="metric-value">{ping_avg:.1f} ms</div>
    </div>
    <div class="metric-box">
      <div class="metric-label">Avg Jitter</div>
      <div class="metric-value">{jitter_avg:.1f} ms</div>
    </div>
    <div class="metric-box">
      <div class="metric-label">Packet Loss</div>
      <div class="metric-value {packet_loss_color}">{packet_loss_avg:.2f}%</div>
    </div>
  </div>

  <img class="chart" src="data:image/png;base64,{chart_base64}" alt="Speed Chart" />
  
  <div class="log-section">
    <div class="log-title">📋 Detailed Test Results ({len(test_results)} tests)</div>
    <table class="log-table">
      <thead>
        <tr>
          <th>Date</th>
          <th>Time</th>
          <th>📥 DL</th>
          <th>📤 UL</th>
          <th>⚡ Ping</th>
          <th>📶 Jitter</th>
          <th>📉 Loss</th>
          <th>🌐 Server</th>
        </tr>
      </thead>
      <tbody>
{log_html_rows}
      </tbody>
    </table>
  </div>
  
  <div class="footer">
    <p>🤖 Automated Speed Monitoring System</p>
    <p><strong>Threshold:</strong> {config['thresholds']['download_mbps']} Mbps Download / {config['thresholds']['upload_mbps']} Mbps Upload</p>
    <p>Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")} • Week {week_num} of {datetime.now().year}</p>
  </div>
</div>
</body>
</html>
"""
    return html


def send_email(config, subject, body, attachment_path=None):
    """Send email via SMTP with SSL/TLS"""

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

    # Attach file if provided
    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, 'rb') as f:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{os.path.basename(attachment_path)}"')
            msg.attach(part)

    # Send email
    try:
        log.info("Sending weekly report email...")

        # Use SMTP_SSL for port 465 or SMTP with STARTTLS for port 587
        if mail.smtp_port == 465:
            # SSL from the start (implicit SSL)
            server = smtplib.SMTP_SSL(mail.smtp_server, mail.smtp_port, timeout=60)
        else:
            # STARTTLS (explicit SSL)
            server = smtplib.SMTP(mail.smtp_server, mail.smtp_port, timeout=60)
            server.starttls()

        server.login(mail.smtp_username, mail.smtp_password)
        server.send_message(msg)
        server.quit()

        log.info("Weekly email with chart and log sent successfully.")
        return True

    except smtplib.SMTPException as e:
        log.error("SMTP Error: %s", e)
        log.info("Check your email credentials and SMTP settings")
        return False
    except Exception as e:
        log.error("Failed to send email: %s", e)
        return False


def main():
    """Main execution function"""

    # Load configuration
    config = load_config()
    script_dir = Path(__file__).parent

    # Calculate week numbers
    week_num = get_iso_week()
    previous_week_num = 52 if week_num == 1 else week_num - 1

    # Set paths
    script_dir = Path(__file__).parent
    log_dir = script_dir / config['paths']['log_directory']
    log_file = log_dir / f"speed_log_week_{week_num}.txt"
    previous_log_file = log_dir / f"speed_log_week_{previous_week_num}.txt"
    chart_base64_file = script_dir / config['paths']['chart_base64']
    python_script = script_dir / "SpeedChart.py"

    # Run Python chart script
    log.info("Running Python chart script...")
    python_exe = sys.executable
    result = subprocess.run([python_exe, str(python_script)], capture_output=True, text=True)
    if result.returncode != 0:
        log.warning("Chart generation had warnings:\n%s", result.stderr)
    else:
        log.info("%s", result.stdout.strip())

    # Read chart base64
    with open(chart_base64_file, 'r', encoding='utf-8') as f:
        chart_base64 = f.read().strip()

    # Parse current week data
    downloads, uploads, pings, jitters, packet_losses = parse_log_file(log_file)

    if not downloads:
        log.warning("No speed test data found for current week")
        sys.exit(1)

    # Parse previous week for comparison
    prev_downloads, prev_uploads, _, _, _ = parse_log_file(previous_log_file)
    prev_dl_avg = sum(prev_downloads) / len(prev_downloads) if prev_downloads else 0
    prev_ul_avg = sum(prev_uploads) / len(prev_uploads) if prev_uploads else 0

    # Parse detailed test results for table
    test_results = parse_log_for_table(log_file)

    # Generate HTML report
    subject = f"📊 Weekly Speed Report - Week {week_num}"
    body = generate_html_report(
        config, week_num, previous_week_num,
        downloads, uploads, pings, jitters, packet_losses,
        prev_dl_avg, prev_ul_avg, test_results, chart_base64
    )

    # Send email
    success = send_email(config, subject, body, str(log_file))

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
