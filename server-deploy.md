# Server Deployment Guide

Complete setup for running the Domain Crawler on a server with all features enabled.

## 🚀 Quick Server Setup

### 1. Start All Services (Scheduled Crawler + Web Interface)
```bash
# Start both scheduled crawler and web viewer
docker-compose --profile scheduled --profile viewer up -d

# Or use the shorthand:
docker-compose --profile scheduled --profile viewer up -d domain-crawler-cron results-viewer
```

### 2. Verify Services are Running
```bash
# Check all containers
docker-compose ps

# Should show:
# - domain-crawler-cron (running scheduled jobs)
# - results-viewer (nginx web interface)
```

### 3. Access Web Interface
```bash
# Web interface available at:
http://your-server-ip:8080

# View results: http://your-server-ip:8080/output/
# View logs: http://your-server-ip:8080/logs/
```

## 📋 Complete Production Setup

### Step 1: Server Preparation
```bash
# Clone/copy project to server
scp -r domain-crawler/ user@server:/opt/
ssh user@server
cd /opt/domain-crawler

# Create required directories
mkdir -p input output logs browser_data cookies

# Set proper permissions
chmod 755 input output logs browser_data cookies
```

### Step 2: Configure Targets
```bash
# Edit your target URLs
nano input/targets.txt

# Example content:
# https://www.reddit.com
# https://www.facebook.com
# https://www.google.com
# https://www.microsoft.com
# https://company1.com
# https://company2.com
```

### Step 3: Start Production Services
```bash
# Start all production services
docker-compose --profile scheduled --profile viewer up -d

# Check logs
docker-compose logs -f domain-crawler-cron
docker-compose logs -f results-viewer
```

### Step 4: Monitor Operations
```bash
# View current status
docker-compose ps

# View crawler logs
docker-compose exec domain-crawler-cron tail -f /app/logs/daily-$(date +%Y%m%d).log

# View nginx access logs
docker-compose logs results-viewer
```

## ⚙️ Configuration Options

### Custom Schedule (Edit crontab file)
```bash
# Edit cron schedule
nano crontab

# Examples:
# Every 6 hours: 0 */6 * * *
# Every hour: 0 * * * *
# Twice daily: 0 6,18 * * *
# Business hours only: 0 9-17 * * 1-5
```

### Custom Crawler Arguments
```bash
# Override default settings
CRAWLER_ARGS="--url-file /app/input/targets.txt --concurrency 5 --output /app/output/domains.txt --log-file /app/logs/custom.log" \
docker-compose --profile scheduled --profile viewer up -d

# Enable dual-stack IPv4/IPv6 for comprehensive domain discovery
CRAWLER_ARGS="--url-file /app/input/targets.txt --dual-stack --concurrency 3 --output /app/output/domains.txt --log-file /app/logs/dual-stack.log" \
docker-compose --profile scheduled --profile viewer up -d
```

### Custom Ports
```bash
# Change web interface port (edit docker-compose.yml)
services:
  results-viewer:
    ports:
      - "80:80"    # Use port 80 instead of 8080
```

## 🔧 Management Commands

### View Real-Time Results
```bash
# Watch domain discoveries in real-time
tail -f output/domains.txt

# Watch logs
tail -f logs/daily-$(date +%Y%m%d).log
```

### Manual Crawler Run
```bash
# Run crawler manually (in addition to scheduled runs)
docker-compose run --rm domain-crawler python domain_crawler.py \
  --url-file /app/input/targets.txt \
  --fqdn-list \
  --output /app/output/manual-$(date +%Y%m%d).txt \
  --log-file /app/logs/manual-$(date +%Y%m%d-%H%M).log

# Manual run with dual-stack for comprehensive discovery
docker-compose run --rm domain-crawler python domain_crawler.py \
  --url-file /app/input/targets.txt \
  --dual-stack \
  --fqdn-list \
  --output /app/output/dual-stack-$(date +%Y%m%d).txt \
  --log-file /app/logs/dual-stack-$(date +%Y%m%d-%H%M).log
```

### Update Target URLs
```bash
# Add new URLs without restarting
echo "https://newcompany.com" >> input/targets.txt

# Or edit the file
nano input/targets.txt

# Changes take effect on next scheduled run
```

### Backup Results
```bash
# Backup all data
tar -czf domain-crawler-backup-$(date +%Y%m%d).tar.gz output/ logs/ browser_data/

# Backup to remote location
rsync -av output/ logs/ user@backup-server:/backups/domain-crawler/
```

## 🔐 Security Considerations

### Firewall Configuration
```bash
# Allow only web interface port
ufw allow 8080/tcp
ufw enable

# Or for internal network only
ufw allow from 192.168.1.0/24 to any port 8080
```

### SSL/TLS (Optional)
```bash
# Add SSL certificate to nginx.conf
server {
    listen 443 ssl;
    ssl_certificate /etc/ssl/certs/your-cert.pem;
    ssl_certificate_key /etc/ssl/private/your-key.pem;
    # ... rest of config
}
```

### Access Control
```bash
# Add basic auth to nginx.conf
location / {
    auth_basic "Domain Crawler";
    auth_basic_user_file /etc/nginx/.htpasswd;
    # ... rest of config
}
```

## 📊 Monitoring & Alerts

### Health Checks
```bash
# Check if crawler is working
curl -f http://localhost:8080/logs/ > /dev/null && echo "Web interface OK"

# Check if domains file is being updated
find output/ -name "domains.txt" -mtime -1 && echo "Domains updated recently"
```

### Log Rotation
```bash
# Add to /etc/logrotate.d/domain-crawler
/opt/domain-crawler/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 644 root root
}
```

### Email Alerts (Optional)
```bash
# Add to crontab for email notifications
0 8 * * * /opt/domain-crawler/scripts/daily-report.sh | mail -s "Domain Crawler Daily Report" admin@company.com
```

## 🚨 Troubleshooting

### Container Issues
```bash
# Restart all services
docker-compose --profile scheduled --profile viewer restart

# View detailed logs
docker-compose --profile scheduled --profile viewer logs

# Check resource usage
docker stats

# If cron container fails with permission errors:
# 1. Rebuild containers after cron fix
docker-compose --profile scheduled --profile viewer down
docker-compose build --no-cache
docker-compose --profile scheduled --profile viewer up -d

# 2. Check cron container logs specifically
docker-compose logs domain-crawler-cron

# 3. If you see "no such file or directory" errors:
# The fix is already applied in docker-compose.yml (inline command instead of entrypoint script)
# Just rebuild and restart as shown above
```

### Browser Issues
```bash
# Clear browser data if crawling fails
rm -rf browser_data/*

# Restart crawler with fresh browser state
docker-compose restart domain-crawler-cron
```

### Network Issues
```bash
# Test DNS resolution
docker-compose exec domain-crawler-cron nslookup google.com

# Test internet connectivity
docker-compose exec domain-crawler-cron wget -O /dev/null https://www.google.com
```

## 📈 Scaling

### Multiple Target Lists
```bash
# Create separate configs for different projects
mkdir -p input/project1 input/project2 output/project1 output/project2

# Run separate instances
docker-compose run --rm domain-crawler python domain_crawler.py \
  --url-file /app/input/project1/targets.txt \
  --output /app/output/project1/domains.txt
```

### High-Volume Processing
```bash
# Increase concurrency for faster processing
CRAWLER_ARGS="--concurrency 8 --url-file /app/input/targets.txt" \
docker-compose --profile scheduled up -d
```

### Resource Limits
```bash
# Add resource limits to docker-compose.yml
services:
  domain-crawler-cron:
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: '1.0'
```

## 🎯 Production Checklist

- [ ] Target URLs configured in `input/targets.txt`
- [ ] Scheduled crawler running: `docker-compose ps | grep cron`
- [ ] Web interface accessible: `curl http://localhost:8080`
- [ ] Logs being generated: `ls -la logs/`
- [ ] Results being updated: `ls -la output/`
- [ ] Firewall configured for port 8080
- [ ] Backup strategy in place
- [ ] Monitoring/alerts configured
- [ ] Log rotation configured

Your server is now running a complete enterprise domain discovery operation with automated scheduling and web-based monitoring!
