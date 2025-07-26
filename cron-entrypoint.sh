#!/bin/bash
set -e

echo "Setting up cron environment..."

# Create the necessary directories with proper permissions
mkdir -p /var/run /var/log
touch /var/log/cron.log

# Install the crontab file if it exists
if [ -f /etc/cron.d/crawler-cron ]; then
    echo "Installing crontab from /etc/cron.d/crawler-cron"
    # Set proper permissions on crontab file
    chmod 0644 /etc/cron.d/crawler-cron
    # Install the crontab
    crontab /etc/cron.d/crawler-cron
    echo "Crontab installed successfully"
else
    echo "Warning: No crontab file found at /etc/cron.d/crawler-cron"
fi

# Ensure output directories exist and have proper permissions
mkdir -p /app/output /app/logs /app/.browser_data
chown -R crawler:crawler /app/output /app/logs /app/.browser_data

echo "Starting cron daemon..."
# Start cron in foreground mode
exec cron -f
