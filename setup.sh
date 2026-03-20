#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "=== SpeedPulse setup ==="
echo ""

if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker is required."
    echo "Install Docker Engine or Docker Desktop first, then rerun this script."
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "ERROR: docker compose is required."
    echo "Install the Docker Compose plugin, then rerun this script."
    exit 1
fi

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example"
fi

mkdir -p data/Log data/Images data/Archive data/Backups

if [ ! -f data/config.json ]; then
    cp config.example.json data/config.json
    echo "Created data/config.json from config.example.json"
fi

touch data/cron.log data/errors.log data/last_alert.txt data/chart_base64.txt

echo ""
echo "Setup complete."
echo ""
echo "Next steps:"
echo "  1. Start the stack: docker compose up -d --build"
echo "  2. Open: http://localhost:8000"
echo "  3. Create your account in the browser if credentials are not configured"
echo "  4. Configure SMTP in Settings or set SMTP_PASSWORD in .env if you want email"
echo ""
