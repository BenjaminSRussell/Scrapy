# ==================================================================
# Consolidated Multi-Stage Dockerfile for All Services
# ==================================================================
# This Dockerfile builds all custom application services:
# - kafka-delta-ingest-builder: Rust builder for Kafka->Delta ingestor
# - kafka-delta-ingest: Final Rust ingestor runtime image
# - crawler-base: Python dependency builder for Scrapy
# - crawler: Final Scrapy crawler runtime image
# - ingestor: Python-based Kafka->Delta ingestor
# - metrics: Metrics exporter service
# ==================================================================

# ============================================
# Stage 1: Rust Ingestor Builder
# ============================================
FROM rust:1.75-slim-bookworm AS kafka-delta-ingest-builder

# Install system dependencies required for building
RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config \
    libssl-dev \
    cmake \
    g++ \
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
FROM debian:bookworm-slim AS kafka-delta-ingest

# Install only runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libssl3 \
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

# Switch to non-root user
USER ingestor

# Expose metrics port
EXPOSE 8001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD pgrep kafka-delta-ingest || exit 1

# The actual command will be provided in docker-compose.yml
ENTRYPOINT ["kafka-delta-ingest"]

# ============================================
# Stage 3: Python Dependency Builder
# ============================================
FROM python:3.12-slim-bookworm AS crawler-base

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
# ============================================
FROM python:3.12-slim-bookworm AS crawler

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

# ============================================
# Stage 5: Python Kafka->Delta Ingestor
# ============================================
FROM python:3.12-slim-bookworm AS ingestor

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir \
    confluent-kafka>=2.3.0 \
    deltalake>=1.1.0 \
    pyarrow>=17.0.0 \
    pandas>=2.0.0 \
    prometheus-client>=0.20.0 \
    python-dotenv>=1.0.0

# Create non-root user
RUN useradd -m -u 1000 ingestor && \
    chown -R ingestor:ingestor /app

# Copy ingestor script
COPY --chown=ingestor:ingestor src/consumers/kafka_delta_ingestor.py .

# Create necessary directories
RUN mkdir -p /data && \
    chown -R ingestor:ingestor /data

# Switch to non-root user
USER ingestor

# Expose Prometheus metrics port
EXPOSE 8001

# Run the ingestor
CMD ["python", "kafka_delta_ingestor.py"]

# ============================================
# Stage 6: Metrics Exporter
# ============================================
FROM python:3.12-slim AS metrics

WORKDIR /app

# Install build dependencies for psutil
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code required by the metrics exporter
COPY src ./src
COPY monitoring/. .

# Command to run the metrics exporter
CMD ["python", "metrics_exporter.py"]