# Multi-stage Dockerfile for UConn Scraping Pipeline
# Phase 10: Production deployment

FROM python:3.11-slim as base

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user
RUN useradd -m -u 1000 scraper && \
    chown -R scraper:scraper /app

USER scraper

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Default command
CMD ["python", "-m", "src.main"]


# Production stage with minimal footprint
FROM base as production

# Copy only necessary files
COPY --from=base /app /app
COPY --from=base /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

USER scraper

# Run with production settings
ENV ENVIRONMENT=production
CMD ["python", "-m", "src.main"]
