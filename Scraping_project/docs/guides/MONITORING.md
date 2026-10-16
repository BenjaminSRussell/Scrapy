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

`alerting_rules.yml` has 45 rules in 9 groups:

- `kafka_infrastructure`: broker and controller health, under-replicated or offline partitions, consumer lag (`KafkaHighConsumerLag`, `KafkaVeryHighConsumerLag`, `KafkaConsumerLagSLO`).
- `scrapy_application`: `ScrapyAppDown`, no items scraped, error rate, item drops, slow responses, blocked.
- `kafka_pipeline`: Kafka → Delta ingest health, write failures, and `KafkaPoisonMessageRate` (DLQ rate above 0.1/s for 5 minutes; not an SLO).
- `delta_lakehouse_writes`: `DeltaWriteFailuresSustained`, `DeltaWriteQueueSaturated`, `DeltaWriteQueueBacklog`, `DeltaUndomainableRows`.
- `stage2_queue`: Stage 2 starved while Stage 1 is active, unreadable queue.
- `infrastructure`: Redis and Postgres up/memory/pool, soft-ban spike, metrics sink failing.
- `monitoring`: Prometheus storage and scrape failures.
- `data_quality`: throughput drop, open circuit breakers, low Delta writes, pipeline error rate, queue backlog, frontier growth (`FrontierGrowth`, depth already above 1000 and still climbing by more than 5000 over 15m). `FrontierGrowth` does not change `QueueBacklog`.
- `pipeline_slos`: `Stage2SuccessSLO` (Stage 2 accepted rows >= 99% over 24h). Redis depth and Kafka lag stay on `QueueBacklog` and `KafkaConsumerLagSLO`. Numbers, queries, and panels: [PIPELINE_SLOS.md](PIPELINE_SLOS.md).

Every rule carries `summary`, `description`, `impact` and `action` annotations, so the alert itself tells on-call what to check. Runbooks for operator actions are in [docs/runbooks/](../runbooks/).

`QueueBacklog` (`redis_queue_length` > 10000 for 10 minutes) is the alert for Redis queue growth and the Redis depth SLO. The comparison stays `> 10000`. `FrontierGrowth` is a separate climb alert (depth > 1000 and a 15-minute increase > 5000) and does not retune that line. The alert is grouped by `queue` and `site_id`, and the `site` annotation is `{{ $labels.site_id }}`. Producers apply the same default as a high watermark (#196): `REDIS_QUEUE_HIGH_WATERMARK` / `REDIS_QUEUE_LOW_WATERMARK` / `REDIS_QUEUE_BACKPRESSURE_MODE` (`block` or `shed`). `redis_queue_backpressure_total{queue,action}` counts block and shed events on the Scrapy process registry. `monitoring/alerts.yml` is not a Prometheus rule file and is not recreated. The published objectives and the panel for each are in [PIPELINE_SLOS.md](PIPELINE_SLOS.md). The main dashboard stays http://localhost:3000/d/scraping-pipeline-health; the SLO panels are http://localhost:3000/d/pipeline-slos.

## Site label (#192)

Queue depth (`redis_queue_length`), throughput (`pipeline_urls_processed_total`, `pipeline_urls_processed_per_second`, `scrapy_items_scraped_total`, `scrapy_requests_total`, `scrapy_new_urls_found_per_minute`, `scrapy_stage_urls_processed_total`), and error counts (`pipeline_errors_total`, `scrapy_spider_errors_total`, `scrapy_items_dropped_total`, `scrapy_responses_total`, `scrapy_stage_errors_total`) include a `site_id` label. `QueueBacklog`, `PipelineHighErrorRate`, the Scrapy error-rate alerts, and `ScrapingThroughputDropped` name that site in their annotations.

Cardinality guardrail: the label value is the configured site id only (`SITE_ID`, else `TENANT_ID`, else `REDIS_SITE_ID`, else `default`), the same id `resolve_site_id` uses for seen-sets. It is never a URL, job id, or raw host. Those are not read off the request. An id that fails the site-id pattern is recorded as `rejected`, and the raw string is not copied onto the series. Shipped `config.yml` does not list a site count. No numeric cap was measured, and metrics are not dropped to enforce one.

`Stage2SuccessSLO` stays at 0.99 over 24h. `KafkaConsumerLagSLO` stays above 250000 messages. Those two rules are not split by `site_id`.

## Label contract (#243)

Grafana aggregates by label name. A metric family must use the same label names in every process. If one process emits `urls.processed.total` with `stage` and another emits it without `stage`, `sum by (stage)` splits or drops the second series. The same applies to `worker_id` and `domain`.

The base labels are `stage`, `worker_id`, and `domain`. `site_id` stays on the series that already have it (#192). It is not removed, and it is not a substitute for `domain`.

| Label | Value |
|---|---|
| `stage` | `PIPELINE_STAGE` if that is set, otherwise the call's stage (`stage1`–`stage4`) or `exporter` for the metrics process. A value that is not a short token (`[a-z][a-z0-9_]{0,31}`) is `rejected`. |
| `worker_id` | `WORKER_ID`, otherwise `<hostname>-<pid>`. Same pattern as a site id. A URL or anything else is `rejected`. |
| `domain` | `METRIC_DOMAIN`, otherwise `unknown`. Same pattern as a site id. This is not the request host. Callers cannot pass a domain, URL, job id, or host into the helper. |
| `site_id` | Unchanged (#192): `SITE_ID`, else `TENANT_ID`, else `REDIS_SITE_ID`, else `default`. Callers cannot pass a URL, job id, or host. A value that fails the pattern is `rejected`. |

Series are still recorded when a label is `rejected`. They are not dropped. There is no numeric site cap. No numeric cap was measured.

Scrapy crawl metrics in `src/scrapy_prometheus.py` always include `stage`, `worker_id`, and `domain`, plus the labels they already had (`spider`, and `site_id` on the #192 series, plus `method`, `status_code`, and the other low-cardinality reasons). The Delta Lake manager lifecycle metrics (`delta_manager_*`) stay unlabeled, and they stay unlabeled in every process.

`monitoring/metrics_exporter.py` and `src/common/metrics_manager.py` both emit `urls.processed.total` and `urls.discovered.total`. Both send `stage`, `worker_id`, `domain`, and `site_id`. Queue depth adds `queue`. Error counts add `error_type`. `stage4_http_requests_total` and `stage4_http_failures_total` use that same base set on the Stage 4 worker and on the exporter's zero baseline (`error_type` only on failures). Circuit-breaker series stay `{name, host}` or unlabeled. Delta table gauges stay `{table}`.

The tuples are `SCRAPY_FAMILIES` and `STATSD_FAMILIES` in `src/utils/metric_labels.py`. A test fails if a family is registered or emitted with a different set. `redis_queue_length` is still the queue-depth series. `Stage2SuccessSLO` stays 0.99 over 24h, `QueueBacklog` still fires above 10000, and `KafkaConsumerLagSLO` still fires above 250000.

## Sitemap discovery backend (#593)

`stage1_discovery_backend_total` is a counter with the base labels plus `spider` and `backend`. One increment is recorded each time a discovery backend starts. `backend` is `rustmapper` (the handoff), `python_sitemap` (the default Python walk), or `python_sitemap_fallback` (the Python walk after a FAILED rustmapper handoff). Config and migration steps are in [CONFIGURATION.md](CONFIGURATION.md#sitemap-discovery-backend-rustmapper-handoff-593).

## Per-job notice preferences (#487)

Prometheus expressions are unchanged. `monitoring/alerts.yml` is not a rule file and is not recreated. `Stage2SuccessSLO` has no `bool` modifier: a breach value is the ratio under 0.99, not 1. `QueueBacklog` stays `redis_queue_length > 10000`. `KafkaConsumerLagSLO` stays above 250000. There is no fourth SLO, and `redis_queue_length` is not renamed.

This tree has no job model and no job-metadata column, so nothing is migrated. Operators keep the preference on the job's metadata dict. `src/utils/job_notice_prefs.py` is the shape and the filter. It does not send a webhook.

| Field | Default | Meaning |
|---|---|---|
| `notify_fail` | `true` | Deliver a fail notice |
| `notify_success` | `false` | Deliver a success notice |
| `notify_stall` | `true` | Deliver a stall notice |
| `quiet_hours` | empty (`null`) | Optional pair of local hours, `[start, end)` |

Missing keys keep those defaults, which is today's notices: fail on, success off, stall on, quiet hours empty. The filter drops a success event when `notify_success` is off. It drops any event whose local hour falls inside a set window, including fail and stall. It does not drop fail or stall when those toggles are on and the hour is outside the window.

The window is half-open on the local clock. `[22, 7)` is 22:00 through 06:59 and wraps midnight. `[9, 17)` is 09:00 through 16:59. `null`, an empty list, or `start == end` is no window. A naive datetime is already local. An aware datetime is converted to the machine's local zone before the hour is read.

## Stage 4 speech-to-text (#1101)

`STAGE4_ASR_ENABLED` defaults off. When it is on, Stage 4 reads local audio and text fixtures only. It does not download remote media (that stays on the crawl pipeline, `ASR_ENABLED`).

`src/common/async_asr_processor.py` stays. Stage 4 already imports it only behind `STAGE4_ASR_ENABLED`, which stays off. No further call was added (#444).

| Metric | Type | When it moves |
|---|---|---|
| `asr_jobs_total` | counter | One transcription attempt that called the speech backend |
| `asr_latency` | histogram (seconds) | Duration of those attempts (`asr_latency_sum`, `asr_latency_count`, `asr_latency_bucket`) |
| `asr_failures_total` | counter | An attempt raised or returned an error |

A missing `speech_recognition` install, or `ASR_PROVIDER=none`, is a skip: no job and no failure, and the summary still runs on the other text. Unset `ASR_PROVIDER` uses local whisper. Audio is sent to Google only when `ASR_PROVIDER=google`.

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
