# Monitoring: Prometheus + Jaeger (OpenTelemetry)

This stack is defined at the repo root:

- `docker-compose.production.yml` — Loki, Jaeger, OpenTelemetry Collector
- `monitoring/otel-collector-config.yml` — OTLP receivers on `4317` (gRPC) / `4318` (HTTP), exports traces to Jaeger
- Scrapy app instrumentation — `Scraping_project/src/otel_tracing.py` (default **off / no-op**)

Prometheus metrics remain available via `src.scrapy_prometheus.PrometheusExtension` (typically `:9410/metrics`). Tracing is additive and does not replace metrics.

## Install optional OTEL packages

OpenTelemetry is **not** in the lean core lockfile. Install the optional extra from `Scraping_project/`:

```bash
cd Scraping_project
pip install -e ".[otel]"
```

Without this extra (or without `OTEL_EXPORTER_OTLP_ENDPOINT`), tracing soft-imports fail closed as a no-op — crawls keep working.

## Enable OTEL traces (short crawl)

1. Start the observability stack (requires Docker network `scraping_network`):

   ```bash
   docker network create scraping_network 2>/dev/null || true
   docker compose -f docker-compose.production.yml up -d
   ```

2. Point the Scrapy process at the collector and run a short crawl:

   ```bash
   export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
   export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
   export OTEL_SERVICE_NAME=scrapy-pipeline
   # optional stable id for filtering in Jaeger:
   # export CRAWL_JOB_ID=demo-crawl-001

   cd Scraping_project
   scrapy crawl <spider_name> -s CLOSESPIDER_ITEMCOUNT=5
   ```

   HTTP alternative: `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` and `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`.

3. Open Jaeger UI: [http://localhost:16686](http://localhost:16686)

4. Find the crawl:
   - Service: `scrapy-pipeline` (or your `OTEL_SERVICE_NAME`)
   - Operation: `crawl.<spider_name>` or `pipeline.item`
   - Filter tags: `scrapy.crawl_job_id`, `scrapy.spider`, `scrapy.stage`

When `OTEL_EXPORTER_OTLP_ENDPOINT` is **unset**, or the `[otel]` extra is not installed, the tracer helper and Scrapy extension are no-ops so local lean runs keep working.

## Span attributes

| Attribute | Meaning |
|-----------|---------|
| `scrapy.stage` | e.g. `stage1`, `item_pipeline` (workers may set `stage2`–`stage4`) |
| `scrapy.spider` | Scrapy spider name |
| `scrapy.crawl_job_id` | Correlation id (from `CRAWL_JOB_ID` or auto UUID) |

`OtelItemPipeline` also writes `crawl_job_id` onto items when possible so later stages can propagate the same id.

## Ops: link Prometheus ↔ Jaeger for one failure

**Scenario:** elevated `scrapy_spider_errors_total` / `scrapy_items_dropped_total` on Prometheus `:9410/metrics` during a crawl.

1. Note the approximate time window and spider label from the Prometheus metric.
2. In Jaeger (`:16686`), search that service/spider around the same window.
3. Open the `crawl.<spider>` span (or child `pipeline.item` spans) and filter by `scrapy.crawl_job_id`.
4. Use span tags / `scrapy.close_reason` plus the metrics spike to confirm whether the failure was a spider exception, pipeline drop, or clean close — then drill into that one job id instead of grepping all logs.

## Stage workers (optional)

Stage 2–4 workers can emit spans with the same helper:

```python
from src.otel_tracing import ensure_crawl_job_id, init_tracing, start_span

init_tracing(service_name="stage2-worker")
job = ensure_crawl_job_id()  # honors CRAWL_JOB_ID
with start_span("stage2.run", stage="stage2", crawl_job_id=job):
    ...
```

Set the same `CRAWL_JOB_ID` (and `OTEL_EXPORTER_OTLP_ENDPOINT`) in the worker environment to stitch Scrapy + worker spans in Jaeger.
