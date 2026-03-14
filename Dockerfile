# phantom-ui-navigator/Dockerfile
# Phantom UI Navigator — Production Container
# Python 3.13 + Playwright (Chromium) + FastAPI

FROM python:3.13-slim

# Metadata
LABEL maintainer="phantom-team"
LABEL description="Phantom UI Navigator — AI agent for visual UI automation"

# System dependencies for Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    gnupg \
    libnss3 \
    libxss1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libdrm2 \
    libgbm1 \
    libxshmfence1 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Work directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy application code
COPY . .

# Expose port (Cloud Run uses PORT env var, default 8080)
ENV PORT=8080
EXPOSE $PORT

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/api/health || exit 1

# Run — Cloud Run injects PORT env var
CMD uvicorn api.main:app --host 0.0.0.0 --port $PORT
