FROM python:3.11-slim

# Install system dependencies for Playwright and DNS tools
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libdrm2 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libxss1 \
    libxtst6 \
    xvfb \
    cron \
    dnsutils \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY domain_crawler.py .

# Create necessary directories and set permissions
RUN mkdir -p /app/input /app/output /app/logs /app/.browser_data /app/cookies

# Create non-root user for security
RUN useradd -m -u 1000 crawler

# Install Playwright browsers as root (before switching users)
RUN playwright install chromium
RUN playwright install-deps

# Set ownership of app directory to crawler user
RUN chown -R crawler:crawler /app

# Switch to non-root user
USER crawler

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Default command (can be overridden)
CMD ["python", "domain_crawler.py", "--help"]
