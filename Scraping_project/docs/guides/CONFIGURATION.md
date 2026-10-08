# Configuration guide

The pipeline reads one YAML file, `Scraping_project/config.yml`. There is no `development.yml`, and there are no per-environment files. To use a different file, pass a path to `get_config()`. To change individual values, use the environment variables listed below.

## How the settings are resolved

1. **A dedicated environment variable**, where one exists (see [Environment variables](#environment-variables)). A variable only overrides the key it names. The YAML is not templated, so `${VAR}` inside `config.yml` is not expanded.
2. **`config.yml`.**
3. **The default in the code.** Every lookup passes one: `config.get("stage2.batch_size", 50)`. If `config.yml` is missing, a small built-in default config is used and a warning is logged.

## Reading configuration in code

```python
from src.core.config import get_config

config = get_config()                       # process-wide singleton
redis_host = config.get("redis.host", "localhost")   # dot-notation keys
stage1 = config.get_section("stage1")      # a copy of one section (dict)

snap = config.snapshot()                    # one consistent view for several reads
batch, poll = snap.get("stage2.batch_size"), snap.get("stage2.poll_interval_seconds")
```

`get()` and `get_section()` hand back copies, so editing what they return changes nothing. To change a value at runtime, use `config.set(key, value)`. `config.reload()` re-reads the file. If the file can't be parsed (for example, it was caught half-written), the reload keeps the previous config and returns `False`. The `scrapy_config_generation` gauge is bumped on every reload that succeeds.

## Sections of `config.yml`

The YAML file comments every key in detail. A few keys are documented in the file but never read by the code; the table marks them. This table shows what each section controls and the keys people change most.

| Section | Controls | Keys you'll most often change |
|---|---|---|
| `redis` | Seen-URL sets, the priority queue, the kill switch | `host`, `port`, `db`, `password`, `max_connections` |
| `postgres` | Metrics/error sink | `host`, `port`, `database`, `user`, `password` (prefer `DB_PASSWORD`) |
| `stage1` | Discovery spiders | `allowed_domains`, `write_domain_urls` / `domain_urls_table`, `expand_seeds`, `parse_sitemaps`, `sitemap.*` (index walk limits), `fetch_policy.*` (per-request policy, cookies), `js_confidence_threshold`, `batch_size`, `js_queue_*`, `circuit_breaker_*`, `use_redis_queue`, `priority_boost_keywords`, `noisy_sections`, `depth_spider.*`, `spiders.*` (per-spider Scrapy settings) |
| `stage2` | Page analysis workers | `max_workers`, `per_host_concurrency`, `batch_size`, `poll_interval_seconds`, quality thresholds `min_word_count`, `min_text_to_html_ratio`, `massive_doc_threshold` (characters; at or above it the page goes to Stage 4), retries `max_retries`, `retry_backoff_base`, `retry_on_status_codes` |
| `stage3` | Summarization of normal pages | `max_workers`, `batch_size`, `poll_interval_seconds`, `similarity_threshold` (MinHash dedup), `model_name`, `min_length`, `max_length`, `device` (`cpu`/`cuda`) |
| `stage4` | Large-document summarization | `chunk_size`, `chunk_overlap`, `model_name`, `min_summary_length`, `max_summary_length`, `device`, `max_workers` |
| `kafka` | Producer/consumer for the Kafka → Delta path | `bootstrap_servers`, `topics.*`, `topic_settings.*`, `producer.*`, `consumer.*`, `message_key_field`, `require_idempotence` |
| `delta_lake` | Storage | `base_path`, `queue_maxsize`, `queue_put_timeout_seconds`, `write_retries`, `write_retry_backoff_seconds`, `cast_mode` (`strict` quarantines rows with uncastable values, `coerce` sets them to null), `queue_retention_hours` / `queue_gc_*` (queue-table GC), `z_order_columns`, `checkpoint_interval`, `force_shutdown_timeout`, `tables.*` |
| `message_queues` | Logical queue names between stages | `stage1_to_stage2`, `stage2_to_stage3`, `stage2_to_stage4`, `js_render_*`, `stage*_errors`, `persistent_queues`, `transient_queues` |
| `logging` | Python logging | `level`, which feeds Scrapy's `LOG_LEVEL`; the `LOG_LEVEL` env var takes precedence. `format`, `file`, `max_bytes` and `backup_count` are **not read by the code today**: logs go to stdout, and the shape is chosen with `LOG_FORMAT=text\|json` |
| `monitoring` | Reference values only | `enabled`, `prometheus_port`, `grafana_port` and `metrics.*` are **not read by the code**. The ports really come from `docker-compose.yml` and `monitoring/prometheus.yml` |
| `export` | `cli.py export` and `LakehouseManager.export()` | `batch_size` (rows per scanned batch, which bounds memory), `max_rows_per_file` and `max_bytes_per_file` (0 = no limit; larger exports roll over to `<stem>.part-NNNNN<ext>`). `default_format`, `output_directory` and `compression` are **not read**: pass `--format` and `--output` to `cli.py export` instead |

Some optional features keep their own example files. For instance, `config/entity_summarization.example.yml` is used by the entity summarizer.

## Environment variables

These are the variables the code actually reads (`grep -rn "getenv" src cli.py start.py`):

| Area | Variables |
|---|---|
| Storage | `DELTA_LAKE_PATH` (takes precedence over `delta_lake.base_path`; compose and k8s set it so workers and the exporter share one lake), `DELTA_BACKEND`, `DELTA_ALLOW_HARD_DELETE` |
| Redis | `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` |
| Postgres | `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` |
| Kafka | `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_SECURITY_PROTOCOL`, `KAFKA_SASL_MECHANISM`, `KAFKA_SASL_USERNAME`, `KAFKA_SASL_PASSWORD`, `KAFKA_PRODUCER_ACKS`, `KAFKA_REQUIRE_IDEMPOTENCE`, `KAFKA_DLQ_ENABLED`, `KAFKA_SPILL_DIR`, `DLQ_PATH` |
| Stage workers | `STAGE2_CONCURRENT` / `STAGE3_CONCURRENT` and `STAGE2_BATCH_SIZE` / `STAGE3_BATCH_SIZE` (these override `stage<n>.max_workers` / `stage<n>.batch_size`), `STAGE2_MIN_WORD_COUNT`, `STAGE2_MIN_TEXT_TO_HTML_RATIO`, `STAGE2_MASSIVE_DOC_THRESHOLD`, `STAGE2_PER_HOST_CONCURRENCY`, `STAGE2_MAX_RETRIES`, `STAGE2_MERGE_RETRIES`, `STAGE4_PDF_MAX_PAGES`, `STAGE4_PDF_MAX_RSS_MB`, `STAGE4_INLINE_TEXT_MAX_CHARS` |
| Crawl politeness and safety | `ROBOTSTXT_OBEY`, `ROBOTS_MAX_CRAWL_DELAY`, `RETRY_AFTER_MAX_DELAY`, `SOFT_BAN_*`, `SSRF_GUARD_ENABLED`, `SSRF_RESOLVE_DNS`, `SSRF_ALLOWED_HOSTS`, `CRAWL_KILL_SWITCH`, `CRAWL_JOB_ID` |
| Rendering | `PLAYWRIGHT_MAX_CONTEXTS`, `PLAYWRIGHT_MAX_PAGES_PER_CONTEXT` |
| Observability | `LOG_LEVEL`, `STATSD_HOST`, `STATSD_PORT`, `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL` |
| ML services | `ZSC_*` (zero-shot classifier service), `ASR_PROVIDER` |

Keep secrets such as `DB_PASSWORD`, `REDIS_PASSWORD` and `KAFKA_SASL_PASSWORD` in the environment or a secret store, never in `config.yml`. See [SECURITY.md](../../../SECURITY.md).
