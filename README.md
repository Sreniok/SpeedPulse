# SpeedPulse

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](docker-compose.yml)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org)

SpeedPulse is a self-hosted internet monitoring tool with a web dashboard, scheduled speed tests, alerts, and weekly reports.

## What It Does

- Runs scheduled speed tests (download, upload, ping, jitter, packet loss)
- Shows live data and charts in a web dashboard
- Sends alerts when values cross thresholds
- Sends weekly report emails
- Stores historical results and rotates old logs

## Quick Start (Docker Recommended)

### Option A: Pre-built image (fastest)

```bash
mkdir speedpulse && cd speedpulse
curl -fsSL https://raw.githubusercontent.com/Sreniok/speedpulse/main/compose.deploy.yml -o docker-compose.yml
docker compose up -d
```

Open `http://localhost:8000`.

On first run, `.env` and `data/config.json` are created automatically.

### Option B: Build from source (this repo)

```bash
git clone https://github.com/Sreniok/speedpulse.git
cd speedpulse
docker compose up -d --build
```

## First-Time Setup Checklist

1. Open dashboard: `http://localhost:8000`
2. Create an account from `/register` if no credentials are set
3. Go to **Settings** and configure:
   - account details
   - speed thresholds
   - test schedule
   - weekly report schedule
4. Configure email:
   - set `SMTP_PASSWORD` in `.env`
   - set SMTP server/from/to in **Settings**

## Common Commands

```bash
# Start / stop

docker compose up -d
docker compose down

# Rebuild after code changes
docker compose up -d --build

# Logs
docker compose logs -f
docker compose logs -f dashboard
docker compose logs -f scheduler

# Update to latest image (pre-built setup)
docker compose pull
docker compose up -d
```

If you cloned this repo, you can also use:

```bash
make up
make down
make logs
make password
```

## Important Files

- `.env`: auth, secrets, port, SMTP password
- `config.json` (or `data/config.json` in deploy compose): app settings
- `Log/` or `data/Log/`: speed test logs
- `Images/` or `data/Images/`: generated charts
- `Archive/` or `data/Archive/`: rotated logs
- `cron.log`, `errors.log`: runtime logs

## Key Environment Variables (`.env`)

```env
DASHBOARD_PORT=8000
DASHBOARD_LOGIN_EMAIL=
DASHBOARD_PASSWORD_HASH=
DASHBOARD_PASSWORD=
APP_SECRET_KEY=<long-random-secret>
AUTH_SALT=<random-hex>
SESSION_COOKIE_SECURE=auto
SMTP_PASSWORD=
```

Notes:

- Keep `DASHBOARD_PASSWORD_HASH` preferred over plain `DASHBOARD_PASSWORD`
- Leave login/password empty only when you want setup mode (`/register`)
- Keep `.env` private (`chmod 600 .env`)

## Manual Script Commands (Optional)

```bash
python3 CheckSpeed.py         # one speed test
python3 SendWeeklyReport.py   # send weekly report now
python3 health_check.py       # run diagnostics
python3 rotate_logs.py        # rotate/archive logs
```

## Troubleshooting

### Dashboard not opening

- Check containers:

```bash
docker compose ps
docker compose logs -f dashboard
```

- Confirm port in `.env` (`DASHBOARD_PORT`)

### Login issues

- If credentials are set, use `/login`
- If no credentials are set, use `/register`
- To create a hash manually:

```bash
python3 generate_password_hash.py
```

### No emails sent

- Confirm `SMTP_PASSWORD` is set in `.env`
- Confirm SMTP host/port/user/from/to in Settings
- Check:

```bash
docker compose logs -f scheduler
docker compose logs -f dashboard
```

## Security Notes

- Do not commit `.env`, credentials, or backup passwords
- Use strong random values for `APP_SECRET_KEY` and `AUTH_SALT`
- Keep `SESSION_COOKIE_SECURE=auto` (or `true` behind HTTPS)

## License

MIT. See [LICENSE](LICENSE).
