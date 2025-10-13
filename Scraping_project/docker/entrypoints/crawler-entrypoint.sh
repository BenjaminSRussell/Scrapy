#!/bin/bash
# ==================================================================
# Entrypoint for Scrapy Crawler Services
# Used by: scrapy-app, stage2-worker, stage3-worker
# ==================================================================
set -e

# Display startup banner
echo "==============================================="
echo "Scrapy Crawler Service Starting"
echo "==============================================="
echo "User: $(whoami)"
echo "Working Directory: $(pwd)"
echo "Python Version: $(python --version)"
echo "==============================================="

# Validate required environment variables
: "${REDIS_HOST:?Error: REDIS_HOST is not set}"
: "${REDIS_PORT:?Error: REDIS_PORT is not set}"
: "${KAFKA_BOOTSTRAP_SERVERS:?Error: KAFKA_BOOTSTRAP_SERVERS is not set}"

# Log configuration (sanitized)
echo "Configuration:"
echo "  REDIS_HOST: ${REDIS_HOST}"
echo "  REDIS_PORT: ${REDIS_PORT}"
echo "  KAFKA_BOOTSTRAP_SERVERS: ${KAFKA_BOOTSTRAP_SERVERS}"
echo "  PYTHONPATH: ${PYTHONPATH}"
echo "==============================================="

# Wait for Redis to be ready
echo "Waiting for Redis to be ready..."
until timeout 1 bash -c "cat < /dev/null > /dev/tcp/${REDIS_HOST}/${REDIS_PORT}" 2>/dev/null; do
  echo "  Redis is unavailable - sleeping"
  sleep 2
done
echo "Redis is ready!"

# Wait for Kafka to be ready (extract host:port)
KAFKA_HOST=$(echo "${KAFKA_BOOTSTRAP_SERVERS}" | cut -d: -f1)
KAFKA_PORT=$(echo "${KAFKA_BOOTSTRAP_SERVERS}" | cut -d: -f2)
echo "Waiting for Kafka to be ready..."
until timeout 1 bash -c "cat < /dev/null > /dev/tcp/${KAFKA_HOST}/${KAFKA_PORT}" 2>/dev/null; do
  echo "  Kafka is unavailable - sleeping"
  sleep 2
done
echo "Kafka is ready!"

echo "==============================================="
echo "Starting application..."
echo "Command: $@"
echo "==============================================="

# Execute the provided command with exec to ensure proper signal handling
exec "$@"
