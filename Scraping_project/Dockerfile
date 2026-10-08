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

# Health check (#542): main process alive + Redis PING when REDIS_HOST is set
# (+ any HEALTHCHECK_TCP=host:port deps). Fails -> container turns unhealthy.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD ["python", "-m", "src.utils.probe", "container"]

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


# ------------------------------------------------------------------
# Release image targets built by .github/workflows/cd-release.yml (#449).
# `docker build --target <name>` only builds the stages a target needs, so the
# Rust toolchain below never runs for `production` (docker-compose.yml).
# ------------------------------------------------------------------

# Crawler / stage workers (Helm scrapyApp, stage2Worker, stage3Worker set their
# own `command:`). Same contents as production.
FROM production as crawler
CMD ["python", "-m", "src.main"]


# StatsD -> Prometheus exporter. Helm runs `python metrics_exporter.py --port ...`
# from WORKDIR /app; the module lives in monitoring/, so ship a thin launcher.
FROM production as metrics
USER root
RUN printf '%s\n' \
    '"""Launcher: Helm/CD run `python metrics_exporter.py` from /app (#449)."""' \
    'import runpy' \
    'runpy.run_path("/app/monitoring/metrics_exporter.py", run_name="__main__")' \
    > /app/metrics_exporter.py && chown scraper:scraper /app/metrics_exporter.py
USER scraper
EXPOSE 9100
CMD ["python", "metrics_exporter.py", "--port", "9100"]


# Rust Kafka -> Delta ingestor (kafka-delta-ingest/). rdkafka is built from
# source (cmake-build feature) with SSL/SASL/zstd. procps is installed because
# the Helm liveness probe runs `pgrep -f ingest`.
# rust:1 (latest stable) rather than the crate's rust-version floor (1.90): the
# locked dependency tree may need a newer compiler than the declared MSRV.
FROM rust:1-slim-bookworm as kafka-delta-ingest-build
# zstd-sys runs bindgen at build time, which needs libclang; librdkafka's
# cmake build (rdkafka "cmake-build") compiles its curl support (curl/curl.h).
RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake make g++ pkg-config perl libssl-dev libsasl2-dev zlib1g-dev libzstd-dev \
    clang libclang-dev libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY kafka-delta-ingest/Cargo.toml kafka-delta-ingest/Cargo.lock ./
COPY kafka-delta-ingest/src ./src
RUN cargo build --release --locked \
    && strip target/release/kafka-delta-ingest

FROM debian:bookworm-slim as kafka-delta-ingest
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libssl3 libsasl2-2 zlib1g libzstd1 libcurl4 procps \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 ingest
COPY --from=kafka-delta-ingest-build /build/target/release/kafka-delta-ingest /usr/local/bin/kafka-delta-ingest
COPY docker/entrypoints/kafka-delta-ingest-entrypoint.sh /usr/local/bin/kafka-delta-ingest-entrypoint.sh
RUN chmod 0755 /usr/local/bin/kafka-delta-ingest-entrypoint.sh
USER ingest
WORKDIR /home/ingest
# `docker run IMAGE --help` and Compose/Helm args go through the entrypoint
# (waits for Kafka when KAFKA_BOOTSTRAP_SERVERS is set, then execs the binary).
ENTRYPOINT ["/usr/local/bin/kafka-delta-ingest-entrypoint.sh"]
CMD ["--help"]
