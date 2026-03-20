#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

./setup.sh

echo ""
echo "Starting SpeedPulse..."
docker compose up -d --build

echo ""
echo "Stack started."
echo "Open http://localhost:8000"

if command -v curl >/dev/null 2>&1; then
    if curl -fsS http://localhost:8000/ready >/dev/null 2>&1; then
        echo "Readiness check passed."
    else
        echo "Readiness endpoint is not ready yet. Use: docker compose logs -f"
    fi
fi

echo ""
echo "Useful commands:"
echo "  docker compose logs -f"
echo "  docker compose down"
echo "  make password"
echo ""
