# Monitoring guide

Monitoring has three parts: metrics (Prometheus, shown in Grafana), alerts (Prometheus rules), and logs (application logs plus the Delta transaction log). For tracing with OpenTelemetry and Jaeger, see the root [MONITORING.md](../../../MONITORING.md).

## Where to look

| What | Where |
|---|---|
| Grafana | http://localhost:3000. The user is `admin` and the password is `GRAFANA_ADMIN_PASSWORD` from `.env`; it is `admin` only when unset, which is fine for local dev only |
| Main dashboard | http://localhost:3000/d/scraping-pipeline-health ("Scraping Pipeline Health") |
| Prometheus | http://localhost:9090. Use *Status → Targets* to see scrape health and *Alerts* to see rule state |
| Scrape config | `monitoring/prometheus.yml`. It scrapes `scrapy_app` (`scraper:9410`), `stage2_worker`, `stage3_worker` and `stage4_worker` (port 9430), `redis`, `postgres` and `prometheus` |
| Alert rules | `monitoring/alerting_rules.yml` and `monitoring/recording_rules.yml`. The Helm chart carries identical copies, and `tests/unit/monitoring/` enforces that |

Grafana provisions its datasources and dashboards from `monitoring/grafana_*.yml` and `monitoring/dashboards/`. If a dashboard is empty, check *Status → Targets* in Prometheus first. A target that is `down` means the service isn't running or its port changed.

## Reading the main dashboard

| Row | Panels | Healthy looks like |
|---|---|---|
| Overview | Items Scraped / s, Active Spiders, Pipeline Health Score, HTTP Error Ratio, Circuit Breakers Open, Delta Write Queue Depth, URLs/s by stage, p95 response time | Items/s above zero while spiders are active, error ratio low, no open breakers, queue depth near 0 |
| Stage 1 – Discovery | Items/s by spider, New URLs/min, URLs skipped by reason, Off-site links, Soft bans / captchas | New URLs taper as the crawl saturates. A jump in soft bans means the site is pushing back (see `ScrapySoftBanSpike`) |
| Error tracking | Errors by type and stage, Spider exceptions, HTTP code distribution, Items dropped/s, Exporter errors | Flat lines. A step change usually lines up with a deploy or a site change |
| Storage & infrastructure | Delta write failures by table, Stage 2 rows by outcome, Kafka → Delta ingest, Service up, Redis queue depths and memory, Network throughput | No write failures, every service `up` = 1, queue depths that rise and fall rather than climb steadily |

The "Unified Dashboard" (`monitoring/dashboards/unified_dashboard.json`) adds Kafka consumer lag and off-site candidate panels for the streaming profile.

## Alerts

`alerting_rules.yml` has 41 rules in 9 groups:

- `kafka_infrastructure`: broker and controller health, under-replicated or offline partitions, consumer lag (`KafkaHighConsumerLag`, `KafkaVeryHighConsumerLag`, `KafkaConsumerLagSLO`).
- `scrapy_application`: `ScrapyAppDown`, no items scraped, error rate, item drops, slow responses, blocked.
- `kafka_pipeline`: Kafka → Delta ingest health and write failures.
- `delta_lakehouse_writes`: `DeltaWriteFailuresSustained`, `DeltaWriteQueueSaturated`, `DeltaWriteQueueBacklog`, `DeltaUndomainableRows`.
- `stage2_queue`: Stage 2 starved while Stage 1 is active, unreadable queue.
- `infrastructure`: Redis and Postgres up/memory/pool, soft-ban spike, metrics sink failing.
- `monitoring`: Prometheus storage and scrape failures.
- `data_quality`: throughput drop, open circuit breakers, low Delta writes, pipeline error rate, queue backlog.

Every rule carries `summary`, `description`, `impact` and `action` annotations, so the alert itself tells on-call what to check. Runbooks for operator actions are in [docs/runbooks/](../runbooks/).

## Logs

- **Application logs.** Logs go to stdout and stderr; nothing reads `logging.file` today. Each line carries `stage`, `worker` and `job` (`CRAWL_JOB_ID`). Set `LOG_FORMAT=json` to get one JSON object per line for Loki or ELK. `LOG_LEVEL` overrides `logging.level`.
- **Containers.** Use `docker-compose logs -f scraper` or `docker-compose logs -f stage2-worker`, and so on.
- **Delta transaction log.** Every table directory has a `_delta_log/` of numbered JSON commits. To see what happened to a table, read its history:

  ```python
  from src.lakehouse.lakehouse_manager import LakehouseManager

  lake = LakehouseManager(start_workers=False)
  for commit in lake.get_table_history("stage2_page_analysis")[:5]:
      print(commit["version"], commit["timestamp"], commit["operation"], commit.get("operationMetrics"))
  ```

  `python cli.py health` prints row and file counts for every table, and `python cli.py validate` checks the tables. Spilled writes, which show up as failed or queue-full batches, sit under `<delta base path>/_write_spill/` as JSONL until `replay_spilled_writes()` loads them.
