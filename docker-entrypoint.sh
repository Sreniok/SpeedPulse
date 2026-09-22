#!/bin/sh
set -e

DATA_DIR="${APP_DATA_DIR:-/app}"
case "$DATA_DIR" in
  /*) ;;
  *) DATA_DIR="/app/$DATA_DIR" ;;
esac
CONFIG_FILE="${CONFIG_PATH:-$DATA_DIR/config.json}"
ENV_FILE="${ENV_PATH:-/app/.env}"

# ── First-run bootstrap (runs as root) ───────────────────

# Guard: detect file bind-mounts that Docker created as empty directories.
for file_path in "$CONFIG_FILE" "$ENV_FILE"; do
  if [ -d "$file_path" ]; then
    echo "ERROR: $file_path is a directory (expected a file)."
    echo "       Ensure the init service ran first or create the file on the host."
    exit 1
  fi
done

# When already running as non-root (e.g. compose user: 1000:1000),
# skip root-only bootstrap and exec the main process directly.
if [ "$(id -u)" != "0" ]; then
  exec "$@"
fi

# Create config.json from the bundled example on first run / empty file
mkdir -p "$(dirname "$CONFIG_FILE")"
if [ ! -s "$CONFIG_FILE" ]; then
  if [ -f /app/config.example.json ]; then
    cp /app/config.example.json "$CONFIG_FILE"
    echo "Created config.json from config.example.json — customise via the dashboard."
  fi
fi

# Generate .env with random secrets when missing or empty
if [ ! -s "$ENV_FILE" ]; then
  SECRET=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 48)
  SALT=$(cat /dev/urandom | tr -dc 'a-f0-9' | head -c 32)
  SECRETS_KEY=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 48)
  DB_PASSWORD=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 24)
  cat > "$ENV_FILE" <<EOF
TZ=UTC
APP_TIMEZONE=UTC

DASHBOARD_PORT=8000
DASHBOARD_LOGIN_EMAIL=
DASHBOARD_PASSWORD_HASH=
DASHBOARD_PASSWORD=
APP_SECRET_KEY=$SECRET
AUTH_SALT=$SALT
SECRETS_MASTER_KEY=$SECRETS_KEY
SESSION_COOKIE_SECURE=auto

SMTP_PASSWORD=
BACKUP_PASSWORD=
POSTGRES_DB=speedpulse
POSTGRES_USER=speedpulse
POSTGRES_PASSWORD=$DB_PASSWORD
DATABASE_URL=postgresql+psycopg://speedpulse:$DB_PASSWORD@postgres:5432/speedpulse
EOF
  echo ""
  echo "=========================================="
  echo "  FIRST RUN — Open the dashboard to"
  echo "  create your administrator account."
  echo "=========================================="
  echo ""
fi

if ! grep -q '^SECRETS_MASTER_KEY=' "$ENV_FILE"; then
  SECRETS_KEY=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 48)
  printf '\nSECRETS_MASTER_KEY=%s\n' "$SECRETS_KEY" >> "$ENV_FILE"
fi

# Ensure runtime directories and files exist
mkdir -p "$DATA_DIR/Log" "$DATA_DIR/Images" "$DATA_DIR/Archive" "$DATA_DIR/Backups"
for f in cron.log errors.log last_alert.txt chart_base64.txt; do
  [ -f "$DATA_DIR/$f" ] || touch "$DATA_DIR/$f"
done

# Fix ownership so the non-root appuser (UID 1000) can write
chown -R 1000:1000 "$DATA_DIR/Log" "$DATA_DIR/Images" "$DATA_DIR/Archive" "$DATA_DIR/Backups" 2>/dev/null || true
for f in "$CONFIG_FILE" "$DATA_DIR/cron.log" "$DATA_DIR/errors.log" "$DATA_DIR/last_alert.txt" "$DATA_DIR/chart_base64.txt" "$ENV_FILE"; do
  [ -f "$f" ] && chown 1000:1000 "$f" 2>/dev/null || true
done

# ── Drop to appuser and exec the main process ────────────
exec setpriv --reuid=1000 --regid=1000 --init-groups -- "$@"
