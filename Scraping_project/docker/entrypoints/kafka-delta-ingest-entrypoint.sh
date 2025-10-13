#!/bin/bash
# ==================================================================
# Entrypoint for Kafka Delta Ingest Service (Rust)
# ==================================================================
set -e

# Display startup banner
echo "==============================================="
echo "Kafka Delta Ingest Service Starting"
echo "==============================================="
echo "User: $(whoami)"
echo "Working Directory: $(pwd)"
echo "==============================================="

# Log configuration (sanitized - never log secrets)
echo "Configuration:"
echo "  RUST_LOG: ${RUST_LOG:-info}"
echo "  RUST_BACKTRACE: ${RUST_BACKTRACE:-1}"
echo "  STATSD_HOST: ${STATSD_HOST:-statsd-exporter}"
echo "  STATSD_PORT: ${STATSD_PORT:-9125}"
echo "==============================================="

# Wait for Kafka to be ready
if [ -n "$KAFKA_BOOTSTRAP_SERVERS" ]; then
  KAFKA_HOST=$(echo "${KAFKA_BOOTSTRAP_SERVERS}" | cut -d: -f1)
  KAFKA_PORT=$(echo "${KAFKA_BOOTSTRAP_SERVERS}" | cut -d: -f2)
  echo "Waiting for Kafka at ${KAFKA_HOST}:${KAFKA_PORT}..."
  until timeout 1 bash -c "cat < /dev/null > /dev/tcp/${KAFKA_HOST}/${KAFKA_PORT}" 2>/dev/null; do
    echo "  Kafka is unavailable - sleeping"
    sleep 2
  done
  echo "Kafka is ready!"
fi

echo "==============================================="
echo "Starting kafka-delta-ingest..."
echo "Command: kafka-delta-ingest $@"
echo "==============================================="

# Execute the kafka-delta-ingest binary with provided arguments
exec kafka-delta-ingest "$@"
