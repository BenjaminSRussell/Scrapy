# Docker Compose profiles

One Compose file (`docker-compose.yml`) plus an observability overlay. The obsolete Compose `version:` key is omitted. Stage 4 stays off `core`: its image is the ML/OCR target (`ml` / `full` profiles, #144), not the lean lock.

## Profiles

| Profile | Services |
|---------|----------|
| `core` | redis, scraper, stage1–stage3 workers, Postgres (least-privilege `scrapy_app` role) |
| `monitoring` | Prometheus, Grafana, redis-exporter, postgres-exporter, ops dashboard (`dashboard/serve.py` on `127.0.0.1:8080`). Redis and Postgres are on this profile too, because the exporters depend on them |
| `streaming` | Kafka (KRaft, no ZooKeeper). Postgres is also on `core` |
| `ml`, `full` | `stage4-worker` only (torch / OCR image). Redis is on these profiles too so the worker can start |
| `observability` | Loki, Jaeger, OpenTelemetry collector in `docker-compose.observability.yml`. Prometheus, Grafana, and StatsD in that file have no profile and start on a bare `up` |

All services share `scraper-network`. The overlay is not a second copy of those services inside `docker-compose.yml`.

Redis is `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` / `REDIS_DB`. Do not set `REDIS_URL`.

## One command (core + monitoring)

```bash
cd Scraping_project
cp .env.example .env   # then set real passwords before sharing the host
docker compose --profile core --profile monitoring up -d
```

`python start.py` and `make docker-up` enable the same two profiles (`COMPOSE_PROFILES`, default `core,monitoring`). They do not start Stage 4, Kafka or the OTEL overlay.

## Validate config (no containers)

```bash
cd Scraping_project
GRAFANA_ADMIN_PASSWORD=dev DB_PASSWORD=dev POSTGRES_PASSWORD=dev APP_DB_PASSWORD=dev \
  docker compose --profile core --profile monitoring config
```

The rendered config includes the dashboard service and published port 8080, and does not include `stage4-worker`.

## Ops dashboard

The monitoring profile publishes the dashboard on loopback port 8080. `dashboard/serve.py` listens on all container interfaces (`--host 0.0.0.0`) because its default bind is loopback-only (#735).

```bash
docker compose --profile monitoring up -d --no-deps dashboard
curl -sf -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/
```

## Optional profiles

```bash
# Kafka. In-network bootstrap is kafka:9092. Host clients use localhost:9092.
docker compose --profile core --profile streaming up -d

# Packaged Prometheus + Grafana + StatsD (no app stack). Loopback 9095 / 3001.
docker compose -f docker-compose.observability.yml up -d

# Loki / Jaeger / OTEL (overlay). Do not expect these from docker-compose.yml alone.
# The command also starts the pack containers (different host ports).
docker compose -f docker-compose.yml -f docker-compose.observability.yml \
  --profile core --profile monitoring --profile observability up -d

# Stage 4 (ML + OCR image). Not started by core.
docker compose --profile ml up -d stage4-worker
```

## Secrets

`.env.example` documents every variable Compose interpolates. Postgres superuser, app role and Grafana admin passwords come from the environment. Local-dev fallbacks remain in the YAML (`POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`) so a checkout still renders; override them before exposing the host. There is no literal `POSTGRES_PASSWORD=postgres` assignment.

## Ports

| Service | Host bind |
|---------|-----------|
| Redis | `127.0.0.1:6379` |
| Ops dashboard | `127.0.0.1:8080` |
| Prometheus | 9090 |
| Grafana | 3000 |
| Postgres | 5432 |
| Kafka | `127.0.0.1:9092` |
| Loki | 3100 |
| Jaeger UI | 16686 |
| OTLP gRPC/HTTP | 4317 / 4318 |
| Pack Prometheus | `127.0.0.1:9095` |
| Pack Grafana | `127.0.0.1:3001` |
| Pack StatsD metrics / UDP | `127.0.0.1:9102` / `127.0.0.1:9125` |
