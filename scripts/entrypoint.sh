#!/bin/sh
set -eu

# Set default cron schedule if not provided
CRON_MINUTE="${CRON_MINUTE:-0}"
CRON_HOUR="${CRON_HOUR:-6}"
CRON_DOM="${CRON_DOM:-*}"
CRON_MONTH="${CRON_MONTH:-*}"
CRON_DOW="${CRON_DOW:-*}"

echo "Setting up cron schedule: ${CRON_MINUTE} ${CRON_HOUR} ${CRON_DOM} ${CRON_MONTH} ${CRON_DOW}"

mkdir -p /app/files /app/state /app/static /var/log /var/lock

# Save all environment variables to a file
env | grep -v "^_" > /etc/environment

# Create crontab file dynamically with environment variable sourcing
cat > /etc/cron.d/app-cron << EOF
# Cron job with dynamic schedule
${CRON_MINUTE} ${CRON_HOUR} ${CRON_DOM} ${CRON_MONTH} ${CRON_DOW} root . /etc/environment && /app/c1get.sh >> /var/log/cron.log 2>&1

# Empty line at the end is required
EOF

# /etc/cron.d lines include the user field, so cron reads this file directly.
chmod 0644 /etc/cron.d/app-cron

# Clean up any existing cron log
rm -f /var/log/cron.log
touch /var/log/cron.log
chmod 0666 /var/log/cron.log

echo "Cron job installed. Starting cron..."
cron

echo "Starting web server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
