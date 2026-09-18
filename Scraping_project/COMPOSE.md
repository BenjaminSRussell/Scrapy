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

## One command (core + monitoring)

```bash
docker compose --profile core --profile monitoring up -d
```

## Optional profiles

```bash
# Add Postgres + Kafka
docker compose --profile core --profile monitoring --profile streaming up -d

# Add Loki / Jaeger / OTEL
docker compose --profile core --profile monitoring --profile observability up -d
```

## Secrets

Copy `.env.example` to `.env`. Grafana uses `${GRAFANA_ADMIN_PASSWORD:-admin}` — set a real password for anything beyond local smoke tests. Postgres uses `DB_*` variables (see `.env.example`).

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
