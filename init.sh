#!/bin/sh
# init.sh — First-run setup for SpeedPulse.
# Creates .env, config.json, directories, and runtime files
# if they don't already exist.  Idempotent — safe to rerun.
#
# Usage (inside Docker init container):
#   /bin/sh /app/init.sh /workspace
#
# Layout on the host:
#   /workspace/.env                  ← secrets (next to docker-compose.yml)
#   /workspace/.initial_credentials  ← first-run password
#   /workspace/data/config.json      ← app config
#   /workspace/data/Log/             ← speed test logs
#   /workspace/data/Images/          ← chart images
#   /workspace/data/Archive/         ← archived logs
set -e

ROOT="${1:-/workspace}"
DATA="$ROOT/data"
# Source templates live in the image at /app/
APP_DIR="/app"

mkdir -p "$DATA"

# ── .env ─────────────────────────────────────────────────
if [ ! -f "$ROOT/.env" ]; then
  cp "$APP_DIR/.env.example" "$ROOT/.env"

  # Generate cryptographically random values
  SECRET=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 48)
  SALT=$(cat /dev/urandom | tr -dc 'a-f0-9' | head -c 32)
  PASSWORD=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 16)

  sed -i "s|replace-with-32-plus-char-random-secret|$SECRET|" "$ROOT/.env"
  sed -i "s|replace-with-random-hex-string|$SALT|" "$ROOT/.env"

  # Replace the placeholder hash with a plain password
  # (the dashboard will auto-hash it on first startup)
  sed -i "s|^DASHBOARD_PASSWORD_HASH=.*|# DASHBOARD_PASSWORD_HASH=  (auto-generated on first start)|" "$ROOT/.env"
  echo "DASHBOARD_PASSWORD=$PASSWORD" >> "$ROOT/.env"

  # Write password to a temporary file with restricted permissions
  CRED_FILE="$ROOT/.initial_credentials"
  printf 'Dashboard username: monitor-admin\nDashboard password: %s\n' "$PASSWORD" > "$CRED_FILE"
  chmod 600 "$CRED_FILE"

  echo ""
  echo "=========================================="
  echo "  FIRST RUN — Credentials Generated"
  echo "=========================================="
  echo ""
  echo "  Credentials saved to: $CRED_FILE"
  echo "  (readable only by current user)"
  echo ""
  echo "  Review the file, then delete it:"
  echo "    cat $CRED_FILE && rm $CRED_FILE"
  echo ""
  echo "  Edit .env to configure SMTP settings"
  echo "  for email reports and alerts."
  echo "=========================================="
  echo ""
fi

# ── config.json ──────────────────────────────────────────
if [ ! -f "$DATA/config.json" ]; then
  cp "$APP_DIR/config.example.json" "$DATA/config.json"
  echo "Created config.json (configure via dashboard Settings)"
fi

# ── Directories ──────────────────────────────────────────
mkdir -p "$DATA/Log" "$DATA/Images" "$DATA/Archive" "$DATA/Backups"

# ── Runtime files (bind-mount targets) ───────────────────
for f in cron.log errors.log last_alert.txt chart_base64.txt; do
  [ -f "$DATA/$f" ] || touch "$DATA/$f"
done

# ── Ensure appuser (UID 1000) owns runtime files ────────
chown -R 1000:1000 "$DATA/Log" "$DATA/Images" "$DATA/Archive" "$DATA/Backups" 2>/dev/null || true
for f in config.json cron.log errors.log last_alert.txt chart_base64.txt; do
  [ -f "$DATA/$f" ] && chown 1000:1000 "$DATA/$f" 2>/dev/null || true
done
[ -f "$ROOT/.env" ] && chown 1000:1000 "$ROOT/.env" 2>/dev/null || true

echo "Setup complete"
