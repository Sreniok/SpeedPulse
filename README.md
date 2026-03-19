# SpeedPulse

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/Sreniok/speedpulse/actions/workflows/ci.yml/badge.svg)](https://github.com/Sreniok/speedpulse/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](docker-compose.yml)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org)

SpeedPulse is a self-hosted internet monitoring tool with a FastAPI dashboard, scheduled speed tests, alerts, weekly/monthly reporting, encrypted backups, and a Docker-first deployment model.

It is designed as a strong portfolio project for Cloud / Platform / DevOps-style roles: containerized services, health semantics, CI quality gates, non-root runtime, and explicit data persistence.

## What It Does

- Runs scheduled speed tests with download, upload, ping, jitter, and packet loss
- Exposes a web dashboard for live status, history, charts, and settings
- Sends alerts and weekly/monthly reports
- Stores operational state in SQLite and measurement history in PostgreSQL
- Rotates archives and supports encrypted backup/restore

## Quick Start

### Option A: Pre-built image

```bash
mkdir speedpulse && cd speedpulse
curl -fsSL https://raw.githubusercontent.com/Sreniok/speedpulse/main/compose.deploy.yml -o docker-compose.yml
docker compose up -d
```

### Option B: Build from source

```bash
git clone https://github.com/Sreniok/speedpulse.git
cd speedpulse
docker compose up -d --build
```

Open `http://localhost:8000`.

On first run an `init` container creates:

- `.env`
- `data/config.json`
- `data/Log/`
- `data/Images/`
- `data/Archive/`
- `data/Backups/`

The stack now also starts:

- `postgres`: measurement database
- `migrate`: schema + legacy log importer

## First-Time Setup

1. Open `http://localhost:8000`
2. Create an account from `/register` if no credentials are configured
3. Go to `Settings`
4. Configure thresholds, schedule, account details, and notifications
5. Set `SMTP_PASSWORD` in `.env` if you want email alerts/reports

## Architecture

```text
               +--------------------+
               +--------------------+
               |   init container   |
               | bootstrap .env and |
               | data/ on first run |
               +---------+----------+
                         |
                         v
               +--------------------+
               |     postgres       |
               | measurement store  |
               +---------+----------+
                         |
                         v
               +--------------------+
               |    migrate job     |
               | schema + log import|
               +----+-----------+---+
                    |           |
                    v           v
             +------+----+  +---+--------+
             | dashboard |  | scheduler  |
             | FastAPI   |  | APScheduler|
             | /health   |  | jobs       |
             | /ready    |  | speedtests |
             +-----------+  +------------+

Host `./data` keeps config, logs, images, archives, and encrypted backup artifacts.
PostgreSQL is the source of truth for measurements, notification history, and runtime/auth state.
```

## Data Persistence

Current persistence model is split by responsibility:

- `.env`: deployment secrets and runtime overrides
- `data/config.json`: application configuration
- PostgreSQL `speed_tests`: measurement source of truth
- PostgreSQL `notification_events`: notification history for the dashboard
- PostgreSQL runtime tables: auth/session/reset/manual-run state
- `data/Log/`: legacy log history and compatibility/backup trail
- `data/Images/`: generated charts/assets
- `data/Archive/`: archived logs and legacy compatibility files
- `data/Backups/`: encrypted backup artifacts

- Runtime application state now uses the shared SQL database (`state_store.py`)
- Historical measurements are now imported into PostgreSQL on startup via `db_migrate.py`
- Speed tests still write log files during the transition, so backups and recovery remain simple
- Backups include an encrypted SQL runtime-state snapshot for restore portability

## Security Model

- Containers run as non-root (`1000:1000`)
- `read_only: true`, `tmpfs: /tmp`, `cap_drop: ALL`
- `no-new-privileges:true`
- Session cookies support `Secure` auto-detection
- FastAPI adds CSP and standard browser security headers
- Secrets stay in `.env`, not in `config.json`
- Backups are encrypted before export

## Health Model

- `/health`: liveness endpoint
- `/ready`: readiness endpoint

Readiness validates:

- config file exists and loads
- storage paths are writable
- measurement database is reachable
- legacy state DB path is writable when fallback mode is used
- `speedtest` binary is available
- email configuration issues are surfaced as warnings

Docker healthchecks use `/ready`, not just a shallow HTTP 200.

## CI / Delivery

- `ci.yml` runs `ruff check`, `pytest`, and `mypy` on maintained typed modules on every push/PR
- `docker-publish.yml` now gates image publishing on the same quality checks
- Docker image uses Python 3.12 consistently across docs, tooling, and runtime
- Ookla CLI is version-pinned in the image build for reproducible builds

## Common Commands

```bash
docker compose up -d
docker compose down
docker compose logs -f
docker compose logs -f dashboard
docker compose logs -f scheduler
docker compose up -d --build
```

If you cloned this repo:

```bash
make up
make down
make logs
make password
```

## Important Files

- `.env`: auth, secrets, SMTP password, deployment overrides
- `data/config.json`: dashboard, thresholds, schedules, notification config
- `data/Log/`: speed test history
- `data/Images/`: generated chart images
- `state_store.py`: runtime/auth/session state storage layer
- `docker-compose.yml`: source deployment
- `compose.deploy.yml`: pre-built image deployment
- `.github/workflows/ci.yml`: test/lint/type-check pipeline

## Manual Commands

```bash
python3 CheckSpeed.py
python3 SendWeeklyReport.py
python3 SendMonthlyReport.py
python3 health_check.py
python3 rotate_logs.py
```

## Roadmap

- Split `web/app.py` into route/service/storage modules
- Replace the lightweight migration runner with a fuller migration workflow if schema complexity grows
- Add OpenTelemetry / structured metrics export
- Add Kubernetes manifests / Helm chart for cluster deployment

## License

MIT. See [LICENSE](LICENSE).
