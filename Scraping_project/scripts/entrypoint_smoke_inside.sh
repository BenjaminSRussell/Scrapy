#!/usr/bin/env bash
# Runs inside the smoke container (#685). Redis is already healthy.
# Each entrypoint must exec a mock command. A failure prints that log and exits nonzero.
set -euo pipefail

for bin in bash timeout python env; do
  if ! command -v "${bin}" >/dev/null 2>&1; then
    echo "entrypoint-smoke: ${bin} is missing from the smoke image" >&2
    exit 1
  fi
done

mkdir -p /tmp/bin
cat > /tmp/bin/kafka-delta-ingest <<'EOF'
#!/bin/bash
echo "SMOKE_READY name=kafka $*"
EOF
chmod +x /tmp/bin/kafka-delta-ingest
export PATH="/tmp/bin:${PATH:-/usr/bin}"

listener_pid=""
cleanup() {
  if [ -n "${listener_pid}" ]; then
    kill "${listener_pid}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

run_case() {
  local name="$1"
  local ready="$2"
  shift 2
  local log="/tmp/entrypoint-smoke-${name}.log"
  echo "=== ${name} ==="
  # Bound the wait. metrics-exporter's Redis loop has no deadline of its own.
  if ! timeout 60 "$@" >"${log}" 2>&1; then
    echo "entrypoint-smoke: ${name} exited nonzero. Logs follow:" >&2
    cat "${log}" >&2
    exit 1
  fi
  cat "${log}"
  if ! grep -F -q -- "${ready}" "${log}"; then
    echo "entrypoint-smoke: ${name} did not reach ready (${ready}). Logs follow:" >&2
    cat "${log}" >&2
    exit 1
  fi
}

run_case crawler "SMOKE_READY name=crawler" \
  bash /entrypoints/crawler-entrypoint.sh \
  bash -c 'echo SMOKE_READY name=crawler'

run_case metrics "SMOKE_READY name=metrics" \
  bash /entrypoints/metrics-exporter-entrypoint.sh \
  bash -c 'echo SMOKE_READY name=metrics'

run_case kafka "SMOKE_READY name=kafka" \
  bash /entrypoints/kafka-delta-ingest-entrypoint.sh --smoke

python -c 'import socket
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", 19092))
s.listen(8)
while True:
    conn, _addr = s.accept()
    conn.close()
' &
listener_pid=$!
sleep 0.5
if ! kill -0 "${listener_pid}" 2>/dev/null; then
  echo "entrypoint-smoke: mock Kafka listener did not start" >&2
  exit 1
fi

run_case crawler-streaming "SMOKE_READY name=crawler-streaming" \
  env KAFKA_BOOTSTRAP_SERVERS=127.0.0.1:19092 \
  bash /entrypoints/crawler-entrypoint.sh \
  bash -c 'echo SMOKE_READY name=crawler-streaming'

stream_log="/tmp/entrypoint-smoke-crawler-streaming.log"
if ! grep -F -q -- "Kafka is ready!" "${stream_log}"; then
  echo "entrypoint-smoke: streaming profile did not report Kafka ready. Logs follow:" >&2
  cat "${stream_log}" >&2
  exit 1
fi

neg_log="/tmp/entrypoint-smoke-negative.log"
set +e
env -u REDIS_HOST REDIS_PORT=6379 \
  timeout 15 bash /entrypoints/crawler-entrypoint.sh true >"${neg_log}" 2>&1
neg_code=$?
set -e
if [ "${neg_code}" -eq 0 ]; then
  echo "entrypoint-smoke: expected nonzero when REDIS_HOST is unset. Logs follow:" >&2
  cat "${neg_log}" >&2
  exit 1
fi
if ! grep -F -q -- "REDIS_HOST is not set" "${neg_log}"; then
  echo "entrypoint-smoke: failure was not actionable (missing REDIS_HOST message). Logs follow:" >&2
  cat "${neg_log}" >&2
  exit 1
fi
echo "negative check ok (exit ${neg_code})"
cat "${neg_log}"
