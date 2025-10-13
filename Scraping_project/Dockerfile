# ==================================================================
# Consolidated Multi-Stage Dockerfile for All Services
# ==================================================================
# This Dockerfile builds all custom application services:
# - kafka-delta-ingest-builder: Rust builder for Kafka->Delta ingestor
# - kafka-delta-ingest: Final Rust ingestor runtime image
# - crawler-base: Python dependency builder for Scrapy
# - crawler: Final Scrapy crawler runtime image (scrapy-runner, stage2_worker, stage3_worker)
# - metrics: Metrics exporter service
#
# Security Hardening:
# - Multi-stage builds to minimize final image size
# - Non-root users for all runtime stages
# - Pinned base image tags for reproducibility
# - Minimal runtime dependencies only
# ==================================================================

# ============================================
# Stage 1: Rust Ingestor Builder
# ============================================
FROM rust:1.90.0-bookworm AS kafka-delta-ingest-builder

# Install system dependencies required for building
RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config \
    libssl-dev \
    libsasl2-dev \
    cmake \
    g++ \
    clang \
    libclang-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /build

# Copy only dependency manifests first for better layer caching
# This allows Docker to cache the dependencies layer if Cargo.toml doesn't change
COPY kafka-delta-ingest/Cargo.toml kafka-delta-ingest/Cargo.lock ./

# Create a dummy main.rs to build dependencies
RUN mkdir -p src && \
    echo "fn main() {}" > src/main.rs && \
    cargo build --release && \
    rm -rf src

# Now copy the actual source code
COPY kafka-delta-ingest/src ./src

# Build the actual application
# Touch main.rs to force rebuild of the application code (not cached dependencies)
RUN touch src/main.rs && \
    cargo build --release --bin kafka-delta-ingest

# Strip debug symbols to reduce binary size
RUN strip /build/target/release/kafka-delta-ingest

# ============================================
# Stage 2: Final Rust Ingestor Runtime
# ============================================
FROM debian:bookworm-20250113-slim AS kafka-delta-ingest

# Install only runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libssl3 \
    libsasl2-2 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd -r ingestor && \
    useradd -r -g ingestor -u 1000 ingestor && \
    mkdir -p /delta /app && \
    chown -R ingestor:ingestor /delta /app

# Set working directory
WORKDIR /app

# Copy the compiled binary from builder stage
COPY --from=kafka-delta-ingest-builder --chown=ingestor:ingestor /build/target/release/kafka-delta-ingest /usr/local/bin/

# Copy entrypoint script
COPY --chown=ingestor:ingestor docker/entrypoints/kafka-delta-ingest-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Switch to non-root user
USER ingestor

# Expose metrics port
EXPOSE 8001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD pgrep kafka-delta-ingest || exit 1

# Use entrypoint script for better initialization
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["ingest", "scraped-items", "./data/delta_lake/scraped_data"]

# ============================================
# Stage 3: Python Dependency Builder
# ============================================
FROM python:3.12.8-slim-bookworm AS crawler-base

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
# Stage 4: Final Scrapy Crawler Runtime
# Used by: scrapy-app, stage2-worker, stage3-worker
# ============================================
FROM python:3.12.8-slim-bookworm AS crawler

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
COPY --from=crawler-base /install /usr/local

# Create non-root user for security
RUN useradd -m -u 1000 scrapy && \
    chown -R scrapy:scrapy /app

# Copy application code
COPY --chown=scrapy:scrapy . .

# Create necessary directories with proper permissions
RUN mkdir -p /app/data /app/logs && \
    chown -R scrapy:scrapy /app/data /app/logs

# Copy entrypoint script
COPY --chown=scrapy:scrapy docker/entrypoints/crawler-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Switch to non-root user
USER scrapy

# Set PYTHONPATH to include /app so that 'src' module can be imported
ENV PYTHONPATH=/app:${PYTHONPATH}

# Expose Prometheus metrics port
EXPOSE 9410

# Health check endpoint (if Prometheus exporter is enabled)
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9410/metrics')" || exit 1

# Use entrypoint script for better initialization
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Default command - run the Scrapy application
# This can be overridden in docker-compose.yml or at runtime
CMD ["python", "cli.py", "scrapy"]

# ============================================
# Stage 5: Metrics Exporter
# ============================================
FROM python:3.12.8-slim-bookworm AS metrics

WORKDIR /app

# Install build dependencies for psutil
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root user for security
RUN useradd -m -u 1001 metrics && \
    chown -R metrics:metrics /app

# Copy the application code required by the metrics exporter
COPY --chown=metrics:metrics src ./src
COPY --chown=metrics:metrics monitoring/. .

# Copy entrypoint script
COPY --chown=metrics:metrics docker/entrypoints/metrics-exporter-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Switch to non-root user
USER metrics

# Set PYTHONPATH to include /app so that 'src' module can be imported
ENV PYTHONPATH=/app:${PYTHONPATH}

# Expose metrics port
EXPOSE 9090

# Use entrypoint script for better initialization
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Command to run the metrics exporter
CMD ["python", "metrics_exporter.py"]
