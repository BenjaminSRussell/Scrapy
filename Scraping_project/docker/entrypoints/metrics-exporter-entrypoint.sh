#!/bin/bash
# ==================================================================
# Entrypoint for Metrics Exporter Service
# ==================================================================
set -e

# Display startup banner
echo "==============================================="
echo "Metrics Exporter Service Starting"
echo "==============================================="
echo "User: $(whoami)"
echo "Working Directory: $(pwd)"
echo "Python Version: $(python --version)"
echo "==============================================="

# Validate required environment variables
: "${REDIS_HOST:?Error: REDIS_HOST is not set}"
: "${REDIS_PORT:?Error: REDIS_PORT is not set}"

# Log configuration (sanitized)
echo "Configuration:"
echo "  REDIS_HOST: ${REDIS_HOST}"
echo "  REDIS_PORT: ${REDIS_PORT}"
echo "  PYTHONPATH: ${PYTHONPATH}"
echo "==============================================="

# Wait for Redis to be ready
echo "Waiting for Redis to be ready..."
until timeout 1 bash -c "cat < /dev/null > /dev/tcp/${REDIS_HOST}/${REDIS_PORT}" 2>/dev/null; do
  echo "  Redis is unavailable - sleeping"
  sleep 2
done
echo "Redis is ready!"

echo "==============================================="
echo "Starting metrics exporter..."
echo "Command: $@"
echo "==============================================="

# Execute the provided command with exec to ensure proper signal handling
exec "$@"
