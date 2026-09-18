# Docker Compose profiles

Unified stack for the scraping pipeline. Obsolete Compose `version:` key is omitted.

## Profiles

| Profile | Services |
|---------|----------|
| `core` | redis, scraper, stage1–stage4 workers |
| `monitoring` | Prometheus, Grafana, ops dashboard (`dashboard/serve.py` on :8080) |
| `streaming` | Postgres + Kafka (defaults match `config.yml`) |
| `observability` | Loki, Jaeger, OpenTelemetry collector (wired from root `docker-compose.production.yml` configs under `../monitoring/`) |

All services share the internal `scraper-network` (no external `scraping_network` required).

## Dependency: worker entrypoints (#142)

The `core` profile runs `python -m src.workers.stage{1,2,3,4}_worker`. Those modules come from the #142 / #262 worker package (`src.workers`). Until that lands on the branch you run against, `docker compose --profile core … up` will fail at container start even if `compose config` succeeds.

## One command (core + monitoring)

```bash
# Requires .env with GRAFANA_ADMIN_PASSWORD (and DB_PASSWORD if using streaming)
cp .env.example .env   # then edit passwords
docker compose --profile core --profile monitoring up -d
```

## Validate config (no containers)

```bash
# CI and local smoke without `up`:
GRAFANA_ADMIN_PASSWORD=dev DB_PASSWORD=dev \
  docker compose --profile core --profile monitoring config
# Expect rendered output to include the dashboard service and published 8080.
```

## Verify ops dashboard on :8080 (after up)

```bash
docker compose --profile core --profile monitoring up -d
# Wait for the dashboard container, then:
curl -sf -o /dev/null -w "%{http_code}\n" http://localhost:8080/
# Expect HTTP 200 (or another non-5xx from dashboard/serve.py).
# Browser: http://localhost:8080/
```

## Optional profiles

```bash
# Add Postgres + Kafka
docker compose --profile core --profile monitoring --profile streaming up -d

# Add Loki / Jaeger / OTEL
docker compose --profile core --profile monitoring --profile observability up -d
```

## Secrets

Copy `.env.example` to `.env`. **Required** (no YAML defaults):

- `GRAFANA_ADMIN_PASSWORD` → `GF_SECURITY_ADMIN_PASSWORD`
- `DB_PASSWORD` → `POSTGRES_PASSWORD` (streaming profile)

`.env.example` uses Compose service DNS (`DB_HOST=postgres`, `KAFKA_BOOTSTRAP_SERVERS=kafka:9092`). Host-side clients should use `localhost` / `localhost:9092` instead.

## Ports

| Service | Port |
|---------|------|
| Redis | 6379 |
| Ops dashboard | 8080 |
| Prometheus | 9090 |
| Grafana | 3000 |
| Postgres | 5432 |
| Kafka | 9092 |
| Loki | 3100 |
| Jaeger UI | 16686 |
| OTLP gRPC/HTTP | 4317 / 4318 |

## Live dashboard check (AC3)

Config smoke alone is not enough — prove `:8080` after bring-up:

```bash
cd Scraping_project
export GRAFANA_ADMIN_PASSWORD=local-dev DB_PASSWORD=local-dev
# Dashboard only (no core workers / #142 entrypoints required):
docker compose --profile monitoring up -d --build --no-deps dashboard
curl -sf http://127.0.0.1:8080/ | head
docker compose --profile monitoring down
```

CI job `dashboard-live` in `.github/workflows/ci-compose.yml` runs this curl smoke.
