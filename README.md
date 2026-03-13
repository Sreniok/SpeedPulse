# 🚀 Internet Speed Monitor

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](docker-compose.yml)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org)

An automated, comprehensive internet speed monitoring system that tracks download/upload speeds, ping, jitter, and packet loss. Features real-time alerting, weekly reports with historical comparison, ISP performance grading, and a web dashboard.

**Platform:** Linux / Docker | **License:** MIT

## ✨ Features

- **Web Dashboard** - Real-time metrics, charts, and settings via browser
- **Automated Speed Tests** - Scheduled tests 3x daily (customizable)
- **Real-Time Alerts** - Instant email/webhook/ntfy notifications when speed drops below thresholds
- **Weekly Reports** - Beautiful HTML email reports with charts and statistics
- **Contract Tracking** - Track ISP contract periods, speeds, and get expiry reminders
- **Historical Tracking** - Week-over-week comparison and trend analysis
- **ISP Performance Grading** - A-F grade based on reliability score
- **Comprehensive Metrics** - Download, Upload, Ping, Jitter, Packet Loss
- **Visual Charts** - Color-coded charts with threshold violations highlighted
- **Error Handling** - Automatic retries, timeout protection, detailed error logging
- **Health Monitoring** - System health checks and diagnostics
- **Hot-Reload** - Config changes apply automatically (no container restarts)

## 📋 Requirements

- **Ubuntu** 20.04 or newer (or other Debian-based Linux)
- **Python** 3.8 or higher (usually pre-installed)
- **Ookla Speedtest CLI (`speedtest`)** (recommended)
- **Internet Connection**
- **Email account** for sending reports

## 🐳 Docker Deployment (Recommended)

This project can run fully in Docker with:

- Internal scheduler service (no host cron)
- FastAPI dashboard with login
- SMTP/auth secrets in `.env`

### Quick Start

1. Clone and start:

   ```bash
   git clone https://github.com/Sreniok/broadband-speed-monitor.git
   cd broadband-speed-monitor
   docker compose up -d --build
   ```

   On the first run an **init** container automatically creates `.env`
   (with generated secrets and a random dashboard password), `config.json`,
   and all required directories.

2. Get your generated password:

   ```bash
   docker compose logs init
   ```

3. Open the dashboard and log in:

   ```text
   http://localhost:8000
   ```

4. _(Optional)_ Edit `.env` to configure SMTP for email reports/alerts,
   then restart:
   ```bash
   nano .env
   docker compose restart
   ```

> **Tip:** Account details, thresholds, schedules, and contract info can all be
> configured from the **Settings** page — no need to hand-edit `config.json`.
>
> **With `make`:** You can also use `make up`, `make down`, `make logs`,
> `make password` — see the [Makefile](Makefile).

### Docker Services

- `dashboard`: web UI + API (login-protected settings, metrics, manual test trigger)
- `scheduler`: runs speed tests, weekly reports, health checks, log rotation, contract reminders

### Notes

- Host `cron` is not required when using Docker compose.
- Legacy encrypted credential scripts (`update_credentials.py`, `credentials_manager.py`) are optional and not required for Docker mode.
- Dashboard startup now fails if auth secrets use default placeholder values.
- Set local file permissions for `.env` to owner-only:
  ```bash
  chmod 600 .env
  ```

## 🔧 Installation

### Quick Install

1. **Clone or download** this repository

2. **Run the setup script**:
   ```bash
   cd internet-speed-monitor
   chmod +x setup.sh
   ./setup.sh
   ```

The setup script will:

- ✅ Install Ookla Speedtest CLI
- ✅ Install Python dependencies (pandas, matplotlib, cryptography)
- ✅ Create necessary directories
- ✅ Update config.json for Linux paths
- ✅ Make scripts executable

### Manual Installation

If you prefer manual setup:

1. **Install Ookla Speedtest CLI**:

   ```bash
   sudo apt install -y ca-certificates curl gnupg
   sudo install -m 0755 -d /etc/apt/keyrings
   curl -fsSL https://packagecloud.io/ookla/speedtest-cli/gpgkey | sudo gpg --dearmor -o /etc/apt/keyrings/ookla-speedtest.gpg
   echo "deb [signed-by=/etc/apt/keyrings/ookla-speedtest.gpg] https://packagecloud.io/ookla/speedtest-cli/ubuntu/ $(. /etc/os-release && echo ${VERSION_CODENAME}) main" \
     | sudo tee /etc/apt/sources.list.d/ookla-speedtest.list >/dev/null
   sudo apt update
   sudo apt install -y speedtest
   ```

   Optional fallback:

   ```bash
   pip3 install speedtest-cli
   ```

2. **Install Python packages**:

   ```bash
   pip3 install pandas matplotlib cryptography
   ```

3. **Configure email credentials**:

   ```bash
   python3 update_credentials.py
   ```

   This creates encrypted credentials using Fernet encryption.

   **For Gmail:** Use an App Password from https://myaccount.google.com/apppasswords

4. **Edit config.json** - Update paths, thresholds, and email settings

5. **Test the setup**:
   ```bash
   python3 CheckSpeed.py
   ```

## 🚀 Deployment (NAS to Ubuntu Server)

If you're developing on a NAS and want to deploy to an Ubuntu server, use the included deployment script that works across all platforms.

### Initial Setup

1. **Edit deployment configuration** in `deploy.sh`:

   ```bash
   SERVER_USER="your_username"      # Your Ubuntu username
   SERVER_HOST="192.168.1.100"      # Your Ubuntu server IP
   SERVER_PATH="/home/your_username/Scripts/Speedtest"
   ```

2. **Setup SSH key** (OPTIONAL - for password-less deployment):

   ```bash
   # Generate SSH key (if you don't have one)
   ssh-keygen -t rsa -b 4096

   # Copy to Ubuntu server
   ssh-copy-id your_username@192.168.1.100
   ```

   **Note:** SSH keys are optional! The script works with passwords too, but you'll need to type your password 2-3 times during deployment. With SSH keys, it's fully automated.

### Deploy from Different Platforms

**From macOS or Linux:**

```bash
./deploy.sh
```

**From Windows:**

```batch
deploy.bat
```

(Requires Git Bash or WSL)

The deployment script will:

- ✅ Sync all project files to Ubuntu server
- ✅ Exclude logs, credentials, and cache files
- ✅ Set proper file permissions
- ✅ Show next steps for server setup

**What gets excluded:**

- Logs (Log/ directory)
- Images (Images/ directory)
- Credentials (credentials.enc, .encryption_key)
- Cache files (**pycache**, \*.pyc)
- Backup files (\*.bak)

**After deployment**, SSH to your Ubuntu server and run:

```bash
cd ~/Scripts/Speedtest
./setup.sh
python3 update_credentials.py
python3 CheckSpeed.py
```

## 📁 Project Structure

```
Speedtest/
├── config.json                    # Your local configuration (gitignored)
├── config.example.json            # Template for new installs
├── requirements.txt               # Python dependencies
├── setup.sh                       # Installation script
├── deploy.sh                      # Deployment script (macOS/Linux)
├── deploy.bat                     # Deployment script (Windows)
├── quickstart.sh                  # Quick start script
├── CheckSpeed.py                  # Main speed test script
├── SendWeeklyReport.py            # Weekly email report
├── SendAlert.py                   # Real-time alert script
├── health_check.py                # System health monitoring
├── SpeedChart.py                  # Chart generation
├── contract_reminder.py            # Contract expiry email reminder
├── credentials_manager.py         # Credential encryption
├── update_credentials.py          # Credential setup
├── rotate_logs.py                 # Log rotation & cleanup
├── annual_report.py               # Annual summary report
├── clean_slate.py                 # Data wipe (ISP switch)
├── credentials.enc                # Encrypted credentials (auto-created)
├── .encryption_key                # Encryption key (auto-created)
├── errors.log                     # Error log (auto-created)
├── cron.log                       # Cron job log (auto-created)
├── Log/
│   └── speed_log_week_XX.txt      # Weekly test logs
├── Images/
│   └── speedchart_week_XX.png     # Generated charts
└── Archive/                       # Archived old logs (auto-created)
    └── speed_log_week_XX.txt.gz   # Compressed old logs
```

## ⚙️ Configuration

### Environment Variables vs config.json

In Docker deployments, environment variables (`.env`) take priority over
`config.json`. The table below shows every setting and where it can be
configured.

| Setting                       | `.env` variable                     | `config.json` key           | Default                 |
| ----------------------------- | ----------------------------------- | --------------------------- | ----------------------- |
| Dashboard login email         | `DASHBOARD_LOGIN_EMAIL`             | —                           | _(required)_            |
| Dashboard password hash       | `DASHBOARD_PASSWORD_HASH`           | —                           | _(required)_            |
| App secret key                | `APP_SECRET_KEY`                    | —                           | _(required, 32+ chars)_ |
| Auth salt                     | `AUTH_SALT`                         | —                           | _(required)_            |
| Session TTL                   | `SESSION_TTL_SECONDS`               | —                           | `43200`                 |
| Secure cookies                | `SESSION_COOKIE_SECURE`             | —                           | `true`                  |
| Login max attempts            | `LOGIN_MAX_ATTEMPTS`                | —                           | `5`                     |
| Login window                  | `LOGIN_WINDOW_SECONDS`              | —                           | `900`                   |
| Login block duration          | `LOGIN_BLOCK_SECONDS`               | —                           | `900`                   |
| SMTP server                   | `SMTP_SERVER`                       | `email.smtp_server`         | —                       |
| SMTP port                     | `SMTP_PORT`                         | `email.smtp_port`           | `465`                   |
| SMTP username                 | `SMTP_USERNAME`                     | `email.from`                | —                       |
| SMTP password                 | `SMTP_PASSWORD`                     | —                           | —                       |
| Email from                    | `EMAIL_FROM`                        | `email.from`                | —                       |
| Email to                      | `EMAIL_TO`                          | `email.to`                  | —                       |
| Speedtest executable          | `SPEEDTEST_EXE`                     | `paths.speedtest_exe`       | `speedtest`             |
| Speedtest server ID           | `SPEEDTEST_SERVER_ID`               | `speedtest.server_id`       | _(auto)_                |
| Timezone                      | `TZ` / `APP_TIMEZONE`               | —                           | `UTC`                   |
| Health check time             | `HEALTH_CHECK_TIME`                 | —                           | `07:00`                 |
| Log rotation time             | `LOG_ROTATION_TIME`                 | —                           | `02:00`                 |
| Run test on startup           | `RUN_STARTUP_SPEEDTEST`             | —                           | `false`                 |
| Cooldown between manual tests | `MANUAL_SPEEDTEST_COOLDOWN_SECONDS` | —                           | `300`                   |
| Log retention (weeks)         | `KEEP_WEEKS`                        | `data_retention.keep_weeks` | `52`                    |
| Log retention (days)          | `KEEP_DAYS`                         | `data_retention.keep_days`  | `30`                    |
| Deploy user                   | `DEPLOY_USER`                       | —                           | _(prompted)_            |
| Deploy host                   | `DEPLOY_HOST`                       | —                           | _(prompted)_            |
| Deploy path                   | `DEPLOY_PATH`                       | —                           | `~/scripts/Speedtest`   |

Settings without an `.env` column are configured only in `config.json`:
`account.*`, `thresholds.*`, `chart.*`, `speedtest.max_retries`,
`speedtest.retry_delay_seconds`, `speedtest.timeout_seconds`,
`scheduling.*`.

Edit `config.json` to customize settings:

### Account Information

```json
{
  "account": {
    "name": "Your Name",
    "number": "Account Number"
  }
}
```

### Thresholds

```json
{
  "thresholds": {
    "download_mbps": 500,          # Alert if download < 500 Mbps
    "upload_mbps": 80,              # Alert if upload < 80 Mbps
    "ping_ms": 20,                  # Alert if ping > 20 ms
    "packet_loss_percent": 1.0      # Alert if packet loss > 1%
  }
}
```

### Email Settings

```json
{
  "email": {
    "from": "stats@yourdomain.com",
    "to": "your@email.com",
    "smtp_server": "smtp.yourdomain.com",
    "smtp_port": 587,
    "send_realtime_alerts": true,
    "alert_cooldown_minutes": 60
  }
}
```

**Email Credentials (Encrypted):**

Secure credential management using Fernet encryption:

```bash
# Run the credential setup script
python3 update_credentials.py
```

You'll be prompted to enter:

- Email address
- Email password (hidden input)
- SMTP server
- SMTP port (465 for SSL, 587 for STARTTLS)

**For Gmail:** Use an App Password from https://myaccount.google.com/apppasswords

Credentials are encrypted and stored in:

- `credentials.enc` - Encrypted credentials
- `.encryption_key` - Encryption key (chmod 600)

**Test Credentials:**

```bash
python3 credentials_manager.py
```

### Scheduling

```json
{
  "scheduling": {
    "test_times": ["08:00", "16:00", "22:00"],
    "weekly_report_time": "Monday 09:00"
  }
}
```

### Speedtest Engine

```json
{
  "paths": {
    "speedtest_exe": "speedtest"
  },
  "speedtest": {
    "server_id": ""
  }
}
```

- `paths.speedtest_exe`: command or full path to speedtest executable.
- `speedtest.server_id`: optional numeric server id to pin a specific test server.
- Environment overrides:
  - `SPEEDTEST_EXE`
  - `SPEEDTEST_SERVER_ID`

Find nearby server IDs:

```bash
speedtest --servers
```

## 🎯 Usage

### Run Manual Speed Test

```bash
python3 CheckSpeed.py
```

**Features:**

- ✅ Automatic retries on failure
- ✅ Timeout protection
- ✅ Extended metrics (jitter, packet loss)
- ✅ Real-time alerting
- ✅ Detailed logging
- ✅ Email alerts on threshold violations

### Generate Weekly Report

```bash
python3 SendWeeklyReport.py
```

**Includes:**

- 📊 Interactive speed chart
- 📈 Week-over-week comparison

### Run System Health Check

```bash
python3 health_check.py
```

**Monitors:**

- 💾 Disk space availability
- 📝 Log file sizes and rotation
- 🚀 Last speed test execution time
- ⚠️ Recent error count (24h window)
- ⚙️ Configuration integrity
- 🔐 Email credentials accessibility

**Alert Conditions:**

- ❌ Disk space below 1 GB
- ❌ Individual log files over 10 MB
- ❌ No speed test in last 24 hours
- ❌ More than 5 errors in 24 hours
- ❌ Missing configuration sections
- ❌ Credential access issues

**Notification:** Sends email alert ONLY when issues are detected

- 🎯 ISP performance grade
- 📋 Detailed test results table
- 📉 Ping, jitter, packet loss stats

### Generate Chart Only

```bash
python3 SpeedChart.py
```

### Update Credentials

```bash
python3 update_credentials.py
```

### Test Credentials

```bash
python3 credentials_manager.py
```

### Maintenance Commands

```bash
# Rotate and archive old logs (keeps last 12 months)
python3 rotate_logs.py

# Generate annual summary report (run at end of contract year)
python3 annual_report.py

# Clean all data when switching ISP (⚠️ deletes everything!)
python3 clean_slate.py
```

## ⏰ Automated Scheduling with Cron

To run speed tests automatically, set up cron jobs:

### Setup Cron Jobs

```bash
crontab -e
```

Add these lines (adjust paths to match your installation):

```bash
# Run speed test at 8 AM, 4 PM, and 10 PM daily
0 8,16,22 * * * cd /opt/scripts/Speedtest && /usr/bin/python3 CheckSpeed.py >> cron.log 2>&1

# Send weekly report every Monday at 8 AM
0 8 * * 1 cd /opt/scripts/Speedtest && /usr/bin/python3 SendWeeklyReport.py >> cron.log 2>&1

# Run health check daily at 7 AM
0 7 * * * cd /opt/scripts/Speedtest && /usr/bin/python3 health_check.py >> cron.log 2>&1
```

**Cron Time Format:**

```
* * * * *
│ │ │ │ │
│ │ │ │ └─── Day of week (0-7, Sunday = 0 or 7)
│ │ │ └───── Month (1-12)
│ │ └─────── Day of month (1-31)
│ └───────── Hour (0-23)
└─────────── Minute (0-59)
```

**More Examples:**

```bash
# Every 2 hours
0 */2 * * * cd /opt/scripts/Speedtest && python3 CheckSpeed.py >> cron.log 2>&1

# Every hour during business hours (9 AM - 5 PM)
0 9-17 * * * cd /opt/scripts/Speedtest && python3 CheckSpeed.py >> cron.log 2>&1

# Every 30 minutes
*/30 * * * * cd /opt/scripts/Speedtest && python3 CheckSpeed.py >> cron.log 2>&1
```

### View and Manage Cron Jobs

```bash
# View your cron jobs
crontab -l

# View cron execution log
tail -f cron.log

# Check if cron service is running
sudo systemctl status cron
```

### Run Cron Job Manually

You can test your cron jobs manually without waiting for the scheduled time:

```bash
# Run the exact command from your cron job
cd /opt/scripts/Speedtest && /usr/bin/python3 CheckSpeed.py >> cron.log 2>&1

# Or run directly (without logging to cron.log)
cd /opt/scripts/Speedtest && python3 CheckSpeed.py

# Run weekly report manually
cd /opt/scripts/Speedtest && python3 SendWeeklyReport.py
```

**Tip:** Copy the exact command from your crontab (`crontab -l`) and run it in your terminal to test if it works correctly.

## 📊 Chart Features

The enhanced `SpeedChart.py` now includes:

- **Color-Coded Points** - Red dots for threshold violations
- **Ping Overlay** - Secondary Y-axis showing ping times
- **Min/Max Markers** - Stars and arrows marking peak values
- **Packet Loss Indicators** - Warning icons for packet loss
- **Weekly Averages** - Displayed on Y-axis with matching colors
- **Individual Speed Labels** - Speed shown at each data point
- **Legend Below Chart** - Better visibility, no overlap

## 🔔 Real-Time Alerting

Alerts are automatically sent when:

- Download speed < threshold
- Upload speed < threshold
- Ping > threshold
- Packet loss > threshold

**Features:**

- ⏱️ Cooldown period (default 60 min) to prevent spam
- 📧 Beautiful HTML email with metrics
- 🎨 Color-coded violation details
- 🔄 Automatic tracking of last alert time

**Example Alert:**

```
⚠️ Internet Speed Alert - Threshold Violations Detected

Detected at: 2025-11-08 14:30:00

Threshold Violations:
• Download: 450 Mbps (threshold: 500 Mbps)
• Ping: 25 ms (threshold: 20 ms)

Current Metrics:
Download: 450 Mbps ❌
Upload: 105 Mbps ✓
Ping: 25 ms ❌
Packet Loss: 0.1% ✓
```

## 📊 Weekly Report Features

**New Enhancements:**

1. **ISP Performance Grade** (A-F)
   - Based on reliability score
   - Percentage of tests above threshold
   - Visual scorecard with gradient

2. **Week-over-Week Comparison**
   - Trend indicators (📈📉➡️)
   - Speed change calculations
   - Historical context

3. **Extended Metrics**
   - Average ping, jitter, packet loss
   - Min/max for all metrics
   - Test count statistics

4. **Enhanced Table**
   - Jitter column
   - Packet loss column
   - Color-coded violations
   - Responsive design

## 📊 Data Storage

Speed test results are stored in:

- **Text logs:** `Log/speed_log_week_XX.txt` - Weekly log files
- **Charts:** `Images/speedchart_week_XX.png` - Visual reports

Logs are organized by ISO week number and include:

- Timestamp
- Download/Upload speeds
- Ping, Jitter, Packet Loss
- Test metadata

## 📦 Log Management

### Automatic Log Rotation

Keep your system clean and organized with the log rotation script:

```bash
python3 rotate_logs.py
```

**What it does:**

- Archives speed test logs older than 12 months (compressed to `.gz`)
- Archives chart images older than 12 months
- Rotates error logs older than 30 days
- Rotates cron logs larger than 10MB or older than 30 days
- Shows storage summary

**Retention policy:**

- Speed test logs: **12 months** (52 weeks) - Perfect for annual ISP contract
- Error/Cron logs: **30 days**
- Archived files: Kept in `Archive/` directory for reference

**Automate with cron** (run monthly):

```bash
# Add to crontab -e
0 2 1 * * cd /opt/scripts/Speedtest && python3 rotate_logs.py >> cron.log 2>&1
```

### Annual Summary Report

Generate a comprehensive year-end report before renewing your ISP contract:

```bash
python3 annual_report.py
```

**Report includes:**

- Full 12-month statistics (averages, min/max, reliability score)
- Monthly performance charts
- Overall ISP grade (A-F)
- Detailed metrics for contract negotiations
- Saved as `annual_summary_YEAR.png`

**Perfect for:**

- End of contract review
- ISP negotiation leverage
- Service quality complaints
- Comparing ISP options

### Clean Slate (Switching ISP)

When changing providers, start fresh with clean data:

```bash
python3 clean_slate.py
```

**⚠️ Warning:** This deletes ALL historical data!

**What it deletes:**

- All speed test logs
- All chart images
- All archived data
- Error and cron logs
- Alert history

**What it keeps:**

- Configuration (`config.json`)
- Email credentials
- All scripts
- Cron jobs (still active)

**Requires confirmation** - Type 'DELETE' to proceed (safety feature)

## 🔒 Security

### Email Credentials

- Stored encrypted using Fernet (symmetric encryption)
- Encryption key stored with restrictive permissions (chmod 600)
- Credentials file: `credentials.enc`
- Encryption key file: `.encryption_key`

### Best Practices

- Store credentials securely
- Use app-specific passwords (Gmail App Password)
- Regularly update credentials
- Review error logs for suspicious activity
- Add to `.gitignore`:
  ```
  credentials.enc
  .encryption_key
  *.log
  ```

## 🐛 Troubleshooting

### Common Issues

**1. speedtest not found**

```bash
sudo apt install -y speedtest
# Fallback only:
pip3 install speedtest-cli
```

**2. Permission denied**

```bash
chmod +x CheckSpeed.py SendAlert.py SendWeeklyReport.py update_credentials.py
```

**3. Python packages missing**

```bash
pip3 install --user pandas matplotlib cryptography
```

**4. Email sending fails**

- Verify SMTP settings in config.json
- Check credentials: `python3 credentials_manager.py`
- For Gmail, use App Password from https://myaccount.google.com/apppasswords
- Ensure TLS/SSL is supported by server
- Check `errors.log` for details

**5. Charts not generating**

- Verify Python installation: `python3 --version`
- Install matplotlib: `pip3 install matplotlib`
- Check error log: `errors.log`
- Run: `python3 SpeedChart.py` manually

**6. Cron jobs not running**

Check cron service:

```bash
sudo systemctl status cron
```

Check cron log:

```bash
grep CRON /var/log/syslog
tail -f ~/Scripts/Speedtest/cron.log
```

**7. Dashboard won't start (Docker)**

- `RuntimeError: AUTH_SALT must be set` — add `AUTH_SALT` to your `.env`:
  ```bash
  python3 -c 'import secrets; print(secrets.token_hex(16))'
  ```
- `RuntimeError: APP_SECRET_KEY must be set` — generate a 32+ char random string
- `RuntimeError: DASHBOARD_LOGIN_EMAIL must be set` — check `.env` has a valid login email
- Check container logs: `docker compose logs dashboard`

**8. Sessions expire after container restart**

- Ensure `AUTH_SALT` and `APP_SECRET_KEY` are set to fixed values in `.env`
  (not auto-generated on each start)

**9. SMTP connection refused in Docker**

- Verify `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` in `.env`
- Port 465 = implicit SSL, port 587 = STARTTLS — make sure `SMTP_PORT` matches
- Test from inside the container:
  ```bash
  docker compose exec dashboard python3 -c "
  import smtplib; s = smtplib.SMTP_SSL('your-server', 465, timeout=10); print('OK')
  "
  ```

**10. Login blocked / too many attempts**

- Wait for the block period to expire (default 15 min)
- Or restart the container to clear the in-memory lockout:
  ```bash
  docker compose restart dashboard
  ```
- Tune via `LOGIN_MAX_ATTEMPTS`, `LOGIN_WINDOW_SECONDS`, `LOGIN_BLOCK_SECONDS`

### Error Logs

Check `errors.log` for detailed error information:

```bash
tail -20 errors.log
```

### Check Logs

View cron execution log:

```bash
tail -f cron.log
```

## 🎨 Customization

### Customize Chart Colors

Edit `SpeedChart.py` - modify colors in plot section:

```python
ax.plot(..., color="your_color_here")
```

### Adjust Thresholds

Edit `config.json` thresholds section for your ISP plan

### Change Email Template

Edit `SendWeeklyReport.py` - modify HTML in the email body section

## 📈 Performance Tips

1. **Reduce Log Size** - Archive old logs regularly or set up logrotate
2. **Optimize Tests** - Adjust cron schedule based on needs
3. **Error Log Rotation** - Clean error log periodically
4. **Monitor Cron Log** - Keep cron.log from growing too large

## 🔄 Updates & Maintenance

### Regular Maintenance

**Weekly:**

- Review error logs: `tail -50 errors.log`
- Check alert emails
- Verify cron jobs are running: `crontab -l`
- Check cron log: `tail -50 cron.log`

**Monthly:**

- Archive old logs
- Review ISP performance trends
- Check disk space usage

**Quarterly:**

- Update speedtest engine and OS packages: `sudo apt update && sudo apt upgrade`
- Update Python packages: `pip3 install --upgrade pandas matplotlib cryptography`
- Review and adjust thresholds

## 📞 Support

### Getting Help

1. Check this README and UBUNTU_SETUP.md
2. Review `errors.log` and `cron.log`
3. Check cron jobs: `crontab -l`
4. Verify credentials: `python3 credentials_manager.py`

### Reporting Issues

When reporting issues, include:

- Relevant lines from `errors.log` and `cron.log`
- Python version: `python3 --version`
- speedtest version: `speedtest --version`
- OS version: `lsb_release -a`

## 📜 License

This project is licensed under the [MIT License](LICENSE).

## 🙏 Acknowledgments

- **Ookla Speedtest CLI** - Speed test engine
- **Python Libraries** - pandas, matplotlib
- **Community** - For feedback and testing

---

**Last Updated:** March 2026  
**Platform:** Linux / Docker  
**License:** MIT

## Quick Reference

### Common Commands

```bash
# Speed Tests
python3 CheckSpeed.py                  # Run manual test
python3 SendWeeklyReport.py            # Send weekly report
python3 SpeedChart.py                  # Generate chart

# Credentials
python3 update_credentials.py          # Setup/update credentials
python3 credentials_manager.py         # Test credentials

# Maintenance
python3 rotate_logs.py                 # Archive old logs (12+ months)
python3 annual_report.py               # Generate year-end summary
python3 clean_slate.py                 # Wipe all data (ISP switch)

# Logs
tail -f cron.log                       # View cron execution
tail -f errors.log                     # View errors

# Cron Jobs
crontab -e                             # Edit cron schedule
crontab -l                             # View cron jobs
```

### File Locations

- **Config:** `config.json`
- **Logs:** `Log/speed_log_week_XX.txt`
- **Charts:** `Images/speedchart_week_XX.png`
- **Archived:** `Archive/speed_log_week_XX.txt.gz`
- **Errors:** `errors.log`
- **Cron Log:** `cron.log`
- **Credentials:** `credentials.enc`, `.encryption_key`

### Recommended Cron Jobs

```bash
# Speed test 3x daily
0 8,16,22 * * * cd /opt/scripts/Speedtest && python3 CheckSpeed.py >> cron.log 2>&1

# Weekly report (Monday 8 AM)
0 8 * * 1 cd /opt/scripts/Speedtest && python3 SendWeeklyReport.py >> cron.log 2>&1

# Monthly log rotation (1st of month, 2 AM)
0 2 1 * * cd /opt/scripts/Speedtest && python3 rotate_logs.py >> cron.log 2>&1
```

---

**🚀 Happy Monitoring!**
