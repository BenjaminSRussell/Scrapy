#!/usr/bin/env bash
# Host side of the entrypoint smoke gate (#685).
# Pulls the smoke images, runs them, requires ready markers, and tears down.
# A startup failure prints container logs and exits nonzero.
# Does not start Scraping_project/docker-compose.yml and does not scrape.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

export COMPOSE_PROJECT_NAME=entrypoint-smoke-685
export COMPOSE_ANSI=never
compose=(docker compose -f docker-compose.entrypoint-smoke.yml)

if ! command -v docker >/dev/null 2>&1; then
  echo "entrypoint-smoke: docker is not available" >&2
  exit 1
fi

cleanup() {
  echo "entrypoint-smoke: tearing down"
  "${compose[@]}" down --remove-orphans --volumes || true
}
trap cleanup EXIT

if grep -E '^[[:space:]]*build:' docker-compose.entrypoint-smoke.yml; then
  echo "entrypoint-smoke: smoke compose must pull images, not build the app" >&2
  exit 1
fi

echo "entrypoint-smoke: pulling test images"
if ! "${compose[@]}" pull; then
  echo "entrypoint-smoke: failed to pull test images (redis:7-alpine, python:3.11-slim)" >&2
  exit 1
fi

set +e
"${compose[@]}" up --abort-on-container-exit --exit-code-from smoke
code=$?
set -e

logs="$("${compose[@]}" logs --no-color 2>&1 || true)"
if [ "${code}" != "0" ]; then
  echo "entrypoint-smoke: startup failed (exit ${code}). Container logs:" >&2
  printf '%s\n' "${logs}" >&2
  exit "${code}"
fi

missing=0
while IFS= read -r token; do
  [ -n "${token}" ] || continue
  if ! grep -F -q -- "${token}" <<<"${logs}"; then
    echo "entrypoint-smoke: missing ready marker: ${token}" >&2
    missing=1
  fi
done <<'EOF'
SMOKE_READY name=crawler
SMOKE_READY name=metrics
SMOKE_READY name=kafka
SMOKE_READY name=crawler-streaming
Kafka is ready!
Redis is ready!
PROFILE: core
PROFILE: streaming
negative check ok
EOF

if [ "${missing}" != "0" ]; then
  echo "entrypoint-smoke: containers did not reach ready. Container logs:" >&2
  printf '%s\n' "${logs}" >&2
  exit 1
fi

echo "entrypoint-smoke: ready"
