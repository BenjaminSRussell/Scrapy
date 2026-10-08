# Grafana dashboards

Every `*.json` file in this directory is provisioned into Grafana automatically (#157).

| File | UID / URL |
|---|---|
| `scraping_pipeline_health.json` | `scraping-pipeline-health`, http://localhost:3000/d/scraping-pipeline-health |
| `unified_dashboard.json` | Off-site discovery panels (legacy) |

## How provisioning works

| | docker-compose (`grafana` service) | Helm (`grafana-deployment.yaml`) |
|---|---|---|
| Datasources | `monitoring/grafana_datasource.yml` → `/etc/grafana/provisioning/datasources/` | rendered in the `-grafana-datasource` ConfigMap |
| Dashboard provider | `monitoring/grafana_dashboards.yml` → `/etc/grafana/provisioning/dashboards/` | `files/monitoring/grafana_dashboards.yml` |
| Dashboard JSON | `monitoring/dashboards/` → `/var/lib/grafana/dashboards` | `files/monitoring/dashboards/*.json` → `/var/lib/grafana/dashboards` |

The provider reads `/var/lib/grafana/dashboards` in both setups. The Helm chart cannot read files outside the chart, so `k8s/helm/scraping-pipeline/files/monitoring/dashboards/` holds copies. `tests/unit/monitoring/test_grafana_provisioning.py` fails if they drift from this directory, so copy any edit over:

```bash
cp monitoring/dashboards/*.json k8s/helm/scraping-pipeline/files/monitoring/dashboards/
```

The default Prometheus datasource has the fixed uid `prometheus`. That is what the dashboard's `$datasource` variable defaults to.

## Scraping Pipeline Health

**Variables:** `$datasource` (Prometheus), `$spider` (from `scrapy_items_scraped_total`), `$stage` (from `scrapy_stage_urls_processed_total`). Both `$spider` and `$stage` are multi-select with an All option.

| Row | Panels (metric) |
|---|---|
| Overview | Items scraped/s (`scrapy_items_scraped_total`); active spiders (`scrapy_spider_opened`); health score (`pipeline:health:score`); HTTP error ratio (`scrapy:error_rate:ratio`); circuit breakers open (`circuit_breaker_open_count`); Delta write queue depth (`delta_write_queue_depth`); URLs/s by stage (`scrapy_stage_urls_processed_total`); p95 response time by spider (`scrapy_response_time_seconds`) |
| Stage 1 - Discovery | Items/s by spider; new URLs per minute (`scrapy_new_urls_found_per_minute`); URLs skipped by reason (`scrapy_urls_skipped_total`); off-site links (`scrapy_offsite_links_found_total`); soft bans by signature (`scrapy_soft_ban_total`) |
| Error Tracking | Errors by stage/type (`scrapy_stage_errors_total`); spider exceptions (`scrapy_spider_errors_total`); HTTP status distribution (`scrapy_responses_total`); items dropped (`scrapy_items_dropped_total`); exporter errors (`pipeline_errors_total`) |
| Storage & Infrastructure | Delta write failures (`delta_write_failures_total`); Stage 2 rows by outcome (`stage2_rows_total`); Kafka→Delta ingest (`kafka_delta_ingest_records_written_total`, `kafka_delta_ingest_write_failures_total`, `kafka_produce_failures_total`); `up` by job; Redis queue depths (`redis_queue_length`); Redis memory (`redis:memory_usage:ratio`); network bytes by spider (`scrapy_downloader_*_bytes_total`) |

Every metric is checked against what the code actually emits: Python `prometheus_client` metrics, StatsD mappings, JMX rules, recording rules and known exporters. `test_alert_metric_catalog.py` runs that check, so a panel can't silently show "No data" because of a renamed or phantom metric.

## Editing

Edit in the Grafana UI (`allowUiUpdates: true`), export the JSON with **Share → Export**, save it here with the same `uid`, and copy it into the Helm files directory.
