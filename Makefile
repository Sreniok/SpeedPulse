.PHONY: up down logs setup clean password

# Default target — setup everything and start
up: setup
	docker compose up -d --build

# Create missing files so Docker bind mounts work correctly
setup:
	@[ -f .env ] || (cp .env.example .env && echo "✔ Created .env from .env.example — edit it with your credentials")
	@mkdir -p data/Log data/Images data/Archive data/Backups
	@[ -f data/config.json ] || (cp config.example.json data/config.json && echo "✔ Created data/config.json from config.example.json")
	@touch data/cron.log data/errors.log data/last_alert.txt data/chart_base64.txt

# Stop services
down:
	docker compose down

# Tail logs
logs:
	docker compose logs -f

# Generate a password hash for DASHBOARD_PASSWORD_HASH
password:
	@python3 generate_password_hash.py

# Remove all runtime data (keeps config and .env)
clean:
	docker compose down -v
	rm -rf data/Log/* data/Images/* data/Archive/* data/Backups/*
	rm -f data/cron.log data/errors.log data/last_alert.txt data/chart_base64.txt
