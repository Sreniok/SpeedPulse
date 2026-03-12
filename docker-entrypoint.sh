#!/bin/sh
set -e

# Validate that .env exists (critical for credentials)
if [ ! -f /app/.env ] && [ ! -f /data/.env ]; then
  echo "WARNING: No .env file found. Dashboard auth and SMTP will not work."
  echo "Copy .env.example to .env and configure it."
fi

# Create config.json from example on first run
if [ ! -f /app/config.json ]; then
  if [ -f /app/config.example.json ]; then
    cp /app/config.example.json /app/config.json
    echo "Created config.json from config.example.json — customise via the dashboard."
  fi
fi

# Ensure runtime directories exist
mkdir -p /app/Log /app/Images /app/Archive

# Ensure runtime files exist (needed for bind-mount targets)
for f in cron.log errors.log last_alert.txt chart_base64.txt; do
  [ -f /app/"$f" ] || touch /app/"$f"
done

exec "$@"
