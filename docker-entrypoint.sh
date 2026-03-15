#!/bin/sh
set -e

# ── First-run bootstrap (runs as root) ───────────────────

# Guard: detect file bind-mounts that Docker created as empty directories
# (happens when host files didn't exist before first 'docker compose up').
for name in config.json .env cron.log errors.log last_alert.txt chart_base64.txt; do
  if [ -d "/app/$name" ]; then
    echo "ERROR: /app/$name is a directory (expected a file)."
    echo "       If using compose.deploy.yml, ensure the init service ran first."
    echo "       Otherwise, create the file on the host before starting:"
    echo "         touch $name"
    echo "       Then restart the container."
    exit 1
  fi
done

# When already running as non-root (e.g. compose user: 1000:1000),
# skip root-only bootstrap and exec the main process directly.
if [ "$(id -u)" != "0" ]; then
  exec "$@"
fi

# Create config.json from the bundled example on first run / empty file
if [ ! -s /app/config.json ]; then
  if [ -f /app/config.example.json ]; then
    cp /app/config.example.json /app/config.json
    echo "Created config.json from config.example.json — customise via the dashboard."
  fi
fi

# Generate .env with random secrets when missing or empty
if [ ! -s /app/.env ]; then
  SECRET=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 48)
  SALT=$(cat /dev/urandom | tr -dc 'a-f0-9' | head -c 32)
  cat > /app/.env <<EOF
TZ=UTC
APP_TIMEZONE=UTC

DASHBOARD_PORT=8000
DASHBOARD_LOGIN_EMAIL=
DASHBOARD_PASSWORD_HASH=
DASHBOARD_PASSWORD=
APP_SECRET_KEY=$SECRET
AUTH_SALT=$SALT
SESSION_COOKIE_SECURE=auto

SMTP_PASSWORD=
BACKUP_PASSWORD=
EOF
  echo ""
  echo "=========================================="
  echo "  FIRST RUN — Open the dashboard to"
  echo "  create your administrator account."
  echo "=========================================="
  echo ""
fi

# Ensure runtime directories and files exist
mkdir -p /app/Log /app/Images /app/Archive /app/Backups
for f in cron.log errors.log last_alert.txt chart_base64.txt; do
  [ -f /app/"$f" ] || touch /app/"$f"
done

# Fix ownership so the non-root appuser (UID 1000) can write
chown -R 1000:1000 /app/Log /app/Images /app/Archive /app/Backups 2>/dev/null || true
for f in config.json cron.log errors.log last_alert.txt chart_base64.txt .env; do
  [ -f /app/"$f" ] && chown 1000:1000 /app/"$f" 2>/dev/null || true
done

# ── Drop to appuser and exec the main process ────────────
exec setpriv --reuid=1000 --regid=1000 --init-groups -- "$@"
