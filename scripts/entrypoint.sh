#!/bin/sh


# Set default cron schedule if not provided
CRON_MINUTE="${CRON_MINUTE:-0}"
CRON_HOUR="${CRON_SCHEDULE:-6}"
CRON_DOM="${CRON_DOM:-*}"
CRON_MONTH="${CRON_MONTH:-*}"
CRON_DOW="${CRON_DOW:-*}"

echo "Setting up cron schedule: ${CRON_MINUTE} ${CRON_HOUR} ${CRON_DOM} ${CRON_MONTH} ${CRON_DOW}"

# Create crontab file dynamically
cat > /etc/cron.d/app-cron << EOF
# Cron job with dynamic schedule
${CRON_MINUTE} ${CRON_HOUR} ${CRON_DOM} ${CRON_MONTH} ${CRON_DOW} /app/c1get.sh >> /var/log/cron.log 2>&1

# Empty line at the end is required
EOF

# Set proper permissions
chmod 0644 /etc/cron.d/app-cron

# Install cron job
crontab /etc/cron.d/app-cron

# Clean up any existing cron log
rm /var/log/cron.log
touch /var/log/cron.log

echo "Cron job installed. Starting cron..."
# Start cron in foreground and tail logs
cron && tail -f /var/log/cron.log
