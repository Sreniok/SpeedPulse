#!/bin/sh
# init.sh — First-run setup for broadband-speed-monitor.
# Creates .env, config.json, directories, and runtime files
# if they don't already exist.  Idempotent — safe to rerun.
set -e

DIR="${1:-/workspace}"

# ── .env ─────────────────────────────────────────────────
if [ ! -f "$DIR/.env" ]; then
  cp "$DIR/.env.example" "$DIR/.env"

  # Generate cryptographically random values
  SECRET=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 48)
  SALT=$(cat /dev/urandom | tr -dc 'a-f0-9' | head -c 32)
  PASSWORD=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 16)

  sed -i "s|replace-with-32-plus-char-random-secret|$SECRET|" "$DIR/.env"
  sed -i "s|replace-with-random-hex-string|$SALT|" "$DIR/.env"

  # Replace the placeholder hash with a plain password
  # (the dashboard will auto-hash it on first startup)
  sed -i "s|^DASHBOARD_PASSWORD_HASH=.*|# DASHBOARD_PASSWORD_HASH=  (auto-generated on first start)|" "$DIR/.env"
  echo "DASHBOARD_PASSWORD=$PASSWORD" >> "$DIR/.env"

  # Write password to a temporary file with restricted permissions
  CRED_FILE="$DIR/.initial_credentials"
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
if [ ! -f "$DIR/config.json" ]; then
  cp "$DIR/config.example.json" "$DIR/config.json"
  echo "Created config.json (configure via dashboard Settings)"
fi

# ── Directories ──────────────────────────────────────────
mkdir -p "$DIR/Log" "$DIR/Images" "$DIR/Archive"

# ── Runtime files (bind-mount targets) ───────────────────
for f in cron.log errors.log last_alert.txt chart_base64.txt; do
  [ -f "$DIR/$f" ] || touch "$DIR/$f"
done

echo "Setup complete"
