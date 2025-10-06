# ==================================================================
# Multi-Stage Dockerfile for Scrapy Application
# ==================================================================
# Stage 1: Builder - Install dependencies
# Stage 2: Final - Copy dependencies and application code
# ==================================================================

# ============================================
# Stage 1: Builder - Install Dependencies
# ============================================
FROM python:3.10-slim-bookworm AS builder

# Set working directory
WORKDIR /build

# Install system dependencies required for building Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    libffi-dev \
    libssl-dev \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    libjpeg-dev \
    libpng-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies to a custom location
# This allows us to copy only the installed packages to the final stage
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ============================================
# Stage 2: Final - Production Image
# ============================================
FROM python:3.10-slim-bookworm

# Set working directory
WORKDIR /app

# Install runtime dependencies only (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    libxslt1.1 \
    libssl3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder stage
COPY --from=builder /install /usr/local

# Create non-root user for security
RUN useradd -m -u 1000 scrapy && \
    chown -R scrapy:scrapy /app

# Copy application code
COPY --chown=scrapy:scrapy . .

# Create necessary directories with proper permissions
RUN mkdir -p /app/data /app/logs && \
    chown -R scrapy:scrapy /app/data /app/logs

# Switch to non-root user
USER scrapy

# Expose Prometheus metrics port
EXPOSE 9410

# Health check endpoint (if Prometheus exporter is enabled)
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9410/metrics')" || exit 1

# Default command - run the Scrapy application
# This can be overridden in docker-compose.yml or at runtime
CMD ["python", "run.py"]