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

# Deployment matrix (#615):
#   core      - Redis + workers (Scraping_project/docker-compose.yml). No Kafka:
#               leave KAFKA_BOOTSTRAP_SERVERS unset and the Kafka wait is skipped.
#   streaming - Kafka enabled (Helm chart sets KAFKA_BOOTSTRAP_SERVERS). The
#               entrypoint waits for the first broker, bounded by KAFKA_WAIT_TIMEOUT.
# REQUIRE_KAFKA=1 makes a missing KAFKA_BOOTSTRAP_SERVERS a hard error.
# Every wait is bounded, so a missing dependency fails the container (and
# surfaces in restarts/CrashLoopBackOff) instead of looping forever.

# Validate required environment variables
: "${REDIS_HOST:?Error: REDIS_HOST is not set}"
: "${REDIS_PORT:?Error: REDIS_PORT is not set}"
REDIS_WAIT_TIMEOUT="${REDIS_WAIT_TIMEOUT:-120}"
KAFKA_WAIT_TIMEOUT="${KAFKA_WAIT_TIMEOUT:-120}"

if [ -z "${KAFKA_BOOTSTRAP_SERVERS:-}" ]; then
  if [ "${REQUIRE_KAFKA:-0}" = "1" ]; then
    echo "Error: REQUIRE_KAFKA=1 but KAFKA_BOOTSTRAP_SERVERS is not set" >&2
    exit 1
  fi
  PROFILE="core"
else
  PROFILE="streaming"
fi

# Log configuration (sanitized)
echo "Configuration:"
echo "  PROFILE: ${PROFILE}"
echo "  REDIS_HOST: ${REDIS_HOST}"
echo "  REDIS_PORT: ${REDIS_PORT}"
echo "  KAFKA_BOOTSTRAP_SERVERS: ${KAFKA_BOOTSTRAP_SERVERS:-<unset: Kafka wait skipped>}"
echo "  PYTHONPATH: ${PYTHONPATH:-}"
echo "==============================================="

# wait_for NAME HOST PORT TIMEOUT_SECONDS
wait_for() {
  local name="$1" host="$2" port="$3" limit="$4" waited=0
  echo "Waiting for ${name} at ${host}:${port} (up to ${limit}s)..."
  until timeout 1 bash -c "cat < /dev/null > /dev/tcp/${host}/${port}" 2>/dev/null; do
    if [ "${waited}" -ge "${limit}" ]; then
      echo "Error: ${name} at ${host}:${port} not reachable after ${limit}s" >&2
      return 1
    fi
    echo "  ${name} is unavailable - sleeping"
    sleep 2
    waited=$((waited + 2))
  done
  echo "${name} is ready!"
}

wait_for "Redis" "${REDIS_HOST}" "${REDIS_PORT}" "${REDIS_WAIT_TIMEOUT}" || exit 1

if [ "${PROFILE}" = "streaming" ]; then
  # First broker of a comma-separated list; strip an optional scheme.
  FIRST_BROKER="${KAFKA_BOOTSTRAP_SERVERS%%,*}"
  FIRST_BROKER="${FIRST_BROKER#*://}"
  KAFKA_HOST="${FIRST_BROKER%:*}"
  KAFKA_PORT="${FIRST_BROKER##*:}"
  if [ "${KAFKA_HOST}" = "${FIRST_BROKER}" ]; then
    KAFKA_PORT=9092
  fi
  wait_for "Kafka" "${KAFKA_HOST}" "${KAFKA_PORT}" "${KAFKA_WAIT_TIMEOUT}" || exit 1
else
  echo "Kafka: skipped (core profile; set KAFKA_BOOTSTRAP_SERVERS to enable streaming)"
fi

echo "==============================================="
echo "Starting application..."
echo "Command: $@"
echo "==============================================="

# Execute the provided command with exec to ensure proper signal handling
exec "$@"
