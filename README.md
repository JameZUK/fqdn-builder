# Domain Crawler

An intelligent domain discovery tool that extracts embedded domain configurations from websites and falls back to web crawling when needed. Built with Playwright for modern web application compatibility, featuring enterprise-grade logging, Docker deployment, and intelligent efficiency optimizations.

## 🚀 Key Features

- **🌐 Dual-Stack IPv4/IPv6 Discovery**: Crawl each URL using both IPv4 and IPv6 for complete domain discovery
- **🧠 Intelligent Output Management**: Loads existing domains, validates via DNS, and only crawls new targets
- **📝 Comprehensive Logging**: Full logging support for unattended runs
- **🐳 Docker Deployment**: Complete containerization with scheduled runs and web viewing
- **⚡ Concurrent Processing**: Process multiple URLs simultaneously with configurable concurrency
- **🔍 DNS Validation**: Automatically removes dead domains and validates existing ones
- **📊 Smart URL Skipping**: Skips URLs with already-known domains for massive efficiency gains
- **🍪 Cookie Persistence**: Maintains login sessions between runs
- **🎯 FQDN Output**: Clean domain lists perfect for security tools

## 📋 Quick Start

### Local Installation
```bash
# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium

# Run on a single URL
python domain_crawler.py https://www.reddit.com --fqdn-list -o domains.txt

# Process multiple URLs with logging
python domain_crawler.py --url-file input/targets.txt --fqdn-list --concurrency 3 \
  --output output/domains.txt --log-file logs/crawler.log
```

### Docker Deployment
```bash
# Create directories
mkdir -p input output logs browser_data

# Add your target URLs
echo "https://www.reddit.com" > input/targets.txt
echo "https://www.facebook.com" >> input/targets.txt

# Build and run (single manual run)
docker-compose up domain-crawler

# For scheduled runs (daily at 2 AM)
docker-compose --profile scheduled up -d domain-crawler-cron

# View results via web interface
docker-compose --profile viewer up -d results-viewer
# Visit http://localhost:8080

# 🚀 COMPLETE SERVER DEPLOYMENT (All Features)
docker-compose --profile scheduled --profile viewer up -d
# This starts: scheduled crawler + web interface together
# Perfect for production servers
```

## 📖 Usage Examples

### Basic Domain Discovery
```bash
# Single URL analysis
python domain_crawler.py https://www.reddit.com

# Save to file with timestamps and metadata
python domain_crawler.py https://www.reddit.com --fqdn-list -o domains.txt

# Process multiple URLs concurrently
python domain_crawler.py --url-file targets.txt --concurrency 5 --fqdn-list -o all_domains.txt
```

### Production Monitoring
```bash
# Full enterprise setup with logging
python domain_crawler.py \
  --url-file input/targets.txt \
  --fqdn-list \
  --concurrency 3 \
  --output output/domains.txt \
  --log-file logs/crawler-$(date +%Y%m%d).log \
  --headless

# The script will:
# 1. Load existing domains from output/domains.txt
# 2. Validate them via DNS (remove dead ones)
# 3. Skip URLs with known domains
# 4. Only crawl truly new targets
# 5. Update output file incrementally
# 6. Log everything to timestamped log file
```

### Dual-Stack IPv4/IPv6 Discovery
```bash
# Comprehensive domain discovery using both IPv4 and IPv6
python domain_crawler.py https://reddit.com --dual-stack --fqdn-list -o domains.txt

# Example output showing enhanced discovery:
# 🌐 Dual-stack mode: Crawling both IPv4 and IPv6
# 🔗 Connecting to https://reddit.com (IPV4)...
# 📊 Results for https://reddit.com (IPV4): 45 domains
# 🔗 Connecting to https://reddit.com (IPV6)...
# 📊 Results for https://reddit.com (IPV6): 52 domains
# 📊 Combined Results: 67 unique domains total

# Batch processing with dual-stack for maximum discovery
python domain_crawler.py --url-file targets.txt --dual-stack --concurrency 2 \
  --fqdn-list -o comprehensive-domains.txt

# Docker deployment with dual-stack enabled
docker-compose run domain-crawler --url-file input/targets.txt --dual-stack \
  --fqdn-list -o output/dual-stack-domains.txt
```

### Efficiency Example
```bash
# First run: Discovers 200 domains from 10 URLs (10 minutes)
python domain_crawler.py --url-file targets.txt --fqdn-list -o domains.txt

# Second run: Validates domains, skips known URLs (30 seconds!)
python domain_crawler.py --url-file targets.txt --fqdn-list -o domains.txt
# Output: "📊 Efficiency: Skipped 8/10 URLs with known domains"
```

## 🔧 Command Line Arguments

### Core Arguments
- `start_url`: URL to analyze (optional if --url-file provided)
- `--url-file`: File containing URLs to process (one per line)
- `-o, --output`: File to save results with comprehensive headers
- `--fqdn-list`: Output clean FQDN list (excludes third-party domains)

### Performance & Efficiency
- `--concurrency`: Concurrent browser instances (1-10, default: 3)
- `-p, --pages`: Max pages to crawl per site (default: 10)

### Logging & Monitoring
- `--log-file`: Path to log file for unattended runs
- `--headless/--no-headless`: Browser visibility (default: headless)

### Session Management
- `--cookies`: Import cookies from JSON file
- `--no-persist-cookies`: Disable cookie storage
- `--clear-cookies`: Clear stored cookies before running
- `--manual-login`: Pause for manual login

### Advanced Options
- `--ipv6`: Enable IPv6 for retry attempts
- `--dual-stack`: Crawl each URL using both IPv4 and IPv6 for complete domain discovery

## 📄 Output File Format

The output files include comprehensive headers with timestamps and configuration details:

```bash
# Organization FQDN List
# Generated by Domain Crawler - excludes third-party domains
# Last updated: 2025-01-15 14:30:25 UTC
#
# Input Sources:
#   1. https://www.reddit.com
#   2. https://www.facebook.com
#   3. https://www.google.com
#
# Statistics:
#   Total organization domains: 247
#   New domains added this run: 12
#   Dead domains removed this run: 2
#   Concurrency level: 3
#   Max pages per site: 10
#
# Script Configuration:
#   Headless mode: True
#   Cookie persistence: True
#   IPv6 support: False
#   URL file: input/targets.txt
#   Log file: logs/crawler.log
#
# Format: One domain per line (FQDN format)
# ================================================

reddit.com
www.reddit.com
old.reddit.com
redd.it
redditmedia.com
...
```

## 🐳 Docker Deployment

### File Structure
```
domain-crawler/
├── docker-compose.yml    # Multi-service deployment
├── Dockerfile           # Container definition
├── nginx.conf          # Web interface config
├── crontab             # Scheduled run configuration
├── domain_crawler.py    # Main script
├── requirements.txt     # Python dependencies
├── input/
│   └── targets.txt     # URLs to crawl
├── output/             # Results directory
├── logs/              # Log files
└── browser_data/      # Cookie storage
```

### Docker Services

**1. Manual Runs**
```bash
docker-compose up domain-crawler
```

**2. Scheduled Runs (Cron)**
```bash
docker-compose --profile scheduled up -d domain-crawler-cron
```
- Daily at 2 AM: Regular domain discovery
- Weekly on Sunday at 3 AM: Full validation with cookie clearing

**3. Web Results Viewer**
```bash
docker-compose --profile viewer up -d results-viewer
```
- Browse results at http://localhost:8080
- View output files and logs via web interface

### Custom Configuration
```bash
# Override default arguments
CRAWLER_ARGS="--url-file /app/input/targets.txt --concurrency 5 --output /app/output/domains.txt --log-file /app/logs/crawler.log" \
docker-compose up domain-crawler

# Mount custom files
docker-compose run -v $(pwd)/my-targets.txt:/app/input/targets.txt domain-crawler
```

## 🧠 Intelligence Features

### 1. Smart URL Skipping
```bash
📋 Loaded 247 existing domains from domains.txt
⏭️ Skipping facebook.com: main domain facebook.com already in existing domains
⏭️ Skipping reddit.com: main domain reddit.com already in existing domains
📊 Efficiency: Skipped 8/10 URLs with known domains
```

### 2. DNS Validation & Cleanup
```bash
🔍 Validating 247 existing domains via DNS...
   ❌ Dead domain removed: old-subdomain.company.com
   ❌ Dead domain removed: deprecated.service.org
🧹 Removed 2 dead domains
✅ 245 domains validated as active
```

### 3. Incremental Updates
```bash
📄 Updated domains.txt:
   ✅ Added 12 new domains
   🗑️ Removed 2 dead domains
   📊 Total domains: 255
   🕒 Last updated: 2025-01-15 14:30:25 UTC
```

## 📊 Performance Benefits

**Traditional Approach:**
- Run 1: 180s (crawl 3 sites)
- Run 2: 180s (crawl same 3 sites)
- Run 3: 180s (crawl same 3 sites)
- **Total: 540 seconds**

**With Intelligence:**
- Run 1: 180s (crawl 3 sites, save domains)
- Run 2: 5s (all URLs skipped!)
- Run 3: 5s (all URLs skipped!)
- **Total: 190 seconds (65% faster!)**

## 🔍 How It Works

1. **Load Existing Domains**: Parses previous output files
2. **DNS Validation**: Concurrent validation of existing domains
3. **Smart URL Filtering**: Skip URLs with known domain patterns
4. **Concurrent Processing**: Multiple browser instances for batch operations
5. **Config Extraction**: Look for embedded JavaScript domain lists
6. **Fallback Crawling**: Intelligent crawling if no config found
7. **Domain Categorization**: Separate organization vs third-party domains
8. **Incremental Updates**: Only write new/changed domains

## 📝 Logging

All operations are logged with timestamps for audit trails:

```bash
2025-01-15 14:30:15,123 - INFO - Domain crawler started
2025-01-15 14:30:15,124 - INFO - Arguments: {'url_file': 'targets.txt', 'concurrency': 3, ...}
2025-01-15 14:30:20,456 - INFO - Loaded 247 existing domains from domains.txt
2025-01-15 14:30:25,789 - INFO - Skipped 8/10 URLs with known domains
2025-01-15 14:30:30,123 - INFO - Updated output file: domains.txt
2025-01-15 14:30:30,124 - INFO - Added 12 new domains, removed 2 dead domains
```

## 🛠️ Requirements

### Local
- Python 3.11+
- playwright>=1.40.0
- beautifulsoup4>=4.12.0
- dnspython>=2.4.0

### Docker
- Docker 20.10+
- Docker Compose 1.29+

## 🎯 Use Cases

### Security & Intelligence
- **Asset Discovery**: Find all organization-owned domains
- **Attack Surface Mapping**: Identify potential targets
- **Monitoring**: Track new domain acquisitions
- **Compliance**: Maintain accurate domain inventories

### DevOps & Infrastructure
- **DNS Management**: Keep DNS records current
- **Certificate Management**: Track domains needing SSL certs
- **Load Balancer Config**: Identify domains for routing
- **CDN Configuration**: Map content delivery endpoints

### Competitive Analysis
- **Market Research**: Track competitor domain changes
- **Brand Monitoring**: Detect new product launches
- **Technology Mapping**: Understand infrastructure patterns
- **Acquisition Tracking**: Identify company acquisitions

## 🏆 Best Practices

### For Regular Monitoring
```bash
# Daily run with logging
python domain_crawler.py --url-file targets.txt --fqdn-list \
  --output domains.txt --log-file logs/daily-$(date +%Y%m%d).log

# Weekly full validation (clear cookies for fresh start)
python domain_crawler.py --url-file targets.txt --fqdn-list \
  --output domains.txt --log-file logs/weekly-$(date +%Y%m%d).log --clear-cookies
```

### For Large-Scale Operations
```bash
# High concurrency for batch processing
python domain_crawler.py --url-file large-targets.txt --concurrency 8 \
  --fqdn-list --output all-domains.txt --log-file logs/batch-run.log

# Process in chunks for very large lists
split -l 50 huge-targets.txt chunk-
for chunk in chunk-*; do
  python domain_crawler.py --url-file $chunk --concurrency 5 \
    --fqdn-list --output domains-$(basename $chunk).txt
done
```

### For High-Security Environments
```bash
# No cookie persistence, clean runs
python domain_crawler.py --url-file targets.txt --no-persist-cookies \
  --fqdn-list --output domains.txt --log-file audit.log

# IPv6 for additional IP diversity
python domain_crawler.py --url-file targets.txt --ipv6 --concurrency 2 \
  --fqdn-list --output domains.txt
```

## 🔧 Troubleshooting

### Common Issues

**Bot Detection (403 Errors)**
- Use `--no-headless` to see what's happening
- Try `--manual-login` for authentication-required sites
- Use different IP addresses or VPN
- Try `--ipv6` for IP diversity

**Performance Issues**
- Reduce `--concurrency` if system resources are limited
- Increase `--pages` for deeper discovery
- Use `--log-file` to track performance bottlenecks

**Docker Issues**
- Ensure proper volume mounts for persistent data
- Check file permissions in mounted directories
- Use `docker-compose logs` to debug container issues

**DNS Validation Failures**
- Check network connectivity
- Verify DNS server accessibility
- Some corporate networks may block DNS queries

### Getting Help

1. **Enable verbose logging**: Use `--log-file` to capture detailed execution
2. **Run with visible browser**: Use `--no-headless` to see browser behavior
3. **Test with simpler targets**: Try `httpbin.org` or `example.com` first
4. **Check Docker logs**: `docker-compose logs domain-crawler`

This tool provides enterprise-grade domain discovery with intelligent optimizations that become more efficient with each run!
