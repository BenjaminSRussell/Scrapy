# UConn Web Scraping Pipeline

A production-grade, type-safe, resilient web scraping pipeline designed for large-scale institutional data collection and analysis.

> **Task guides:** [Configuration](docs/guides/CONFIGURATION.md) • [Running](docs/guides/RUNNING.md) (other domains, resuming after a crash) • [Monitoring](docs/guides/MONITORING.md) • [Data usage](docs/guides/DATA_USAGE.md) (querying Delta Lake, CSV/JSON/Parquet export)

This file is the canonical project doc. The [repository root README](../README.md) is only the entry point and quick start; keep the two in agreement. Run every command from this directory (`Scraping_project/`). From a fresh clone that is `cd Scrapy/Scraping_project`.

## Overview

This is an enterprise-ready multi-stage web scraping system with comprehensive type safety, error handling, caching, and production deployment configurations. The pipeline has evolved through 10 major phases to deliver a scalable, maintainable, and observable system.

## Key Features

### Production-Ready Infrastructure (Phase 10)
- Docker and Kubernetes deployment configurations
- CI/CD pipeline with GitHub Actions
- Prometheus + Grafana monitoring stack
- Automated testing and deployment
- Health checks and alerting

### Type Safety & Data Validation (Phase 6)
- Pydantic models for runtime validation
- MyPy static type checking
- PyArrow schemas for Delta Lake tables
- Type-safe operations throughout the pipeline

### Error Handling & Resilience (Phase 7)
- Hierarchical exception system with categorization
- Retry logic with exponential backoff and jitter
- Circuit breaker pattern for fault tolerance
- Dead letter queue for failed item management
- Comprehensive error context tracking

### Performance & Caching (Phase 8)
- Multi-level caching (L1 memory + L2 Redis)
- HTTP connection pooling
- Performance profiling utilities
- Cache statistics and hit rate tracking

### Comprehensive Testing (Phase 9)
- 25+ unit tests with 100% pass rate
- Pytest configuration with async support
- Mock fixtures for Redis and Delta Lake
- Test coverage tracking

### Multi-Stage Architecture
- **Stage 1**: Discovery and URL extraction using Scrapy spiders
- **Stage 2**: Content analysis and validation
- **Stage 3**: Entity summarization and aggregation
- **Stage 4**: Advanced processing and enrichment

### Data Storage
- **Delta Lake**: Primary data lake for scalable storage with schema validation
- **Redis**: Distributed deduplication, caching, and queue management
- **Type-safe operations**: All data validated with Pydantic models

## Quick Start

Same commands as the [root README](../README.md#-quick-start). Do not use a bare `docker compose up -d`: every service in `docker-compose.yml` is behind a profile, so that starts nothing.

### Prerequisites

- Docker with Compose
- Python **3.11 or 3.12** on the host. CI tests exactly those two (`.github/workflows/main.yml`). The image is `python:3.11-slim`, and `.python-version` pins 3.11. Python 3.14 is not a supported version.
- Kubernetes 1.24+ only for a Helm deploy

### Run the stack

```bash
git clone https://github.com/BenjaminSRussell/Scrapy.git
cd Scrapy/Scraping_project
python start.py
```

`python start.py` sets Compose profiles `core` and `monitoring` (Redis, scraper, stage 1–3 workers, Postgres, Prometheus, Grafana) and waits for Postgres. It does not start Stage 4 (profiles `ml` / `full`), Kafka (`streaming`), or the OpenTelemetry overlay. The equivalent Compose command is in [COMPOSE.md](COMPOSE.md): `docker compose --profile core --profile monitoring up -d`. Add `--reset-delta` to wipe Delta tables and reload seeds. Stop with `python shutdown.py`.

Grafana is at http://localhost:3000 (user `admin`; password is `GRAFANA_ADMIN_PASSWORD` in `.env`, default `admin` for local dev only). Full detail, other domains, and resuming after a crash: [Running guide](docs/guides/RUNNING.md).

### Develop on the host

A virtualenv is for tests and for running workers yourself, not for the Compose path above. Pins match CI; see [CONTRIBUTING.md](../CONTRIBUTING.md).

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r dev-requirements.txt -r ci-tools.txt
```

Stage 4 NLP (`transformers`, `torch`) is optional and heavy (~2 GB). It is not required for tests or the core pipeline, and it does not build an image:

```bash
make install-stage4
# or: pip install -r requirements-stage4.txt
```

### Kubernetes

The supported path is the Helm chart in namespace `scraping-pipeline`. Follow [k8s/README.md](k8s/README.md). `k8s/deployment.yaml` is a smaller manifest (stage1/stage2 workers and Redis only, namespace `uconn-scraper`), not the full stack.

### Run one worker yourself

```bash
python -m src.workers.stage1_worker
python -m src.workers.stage2_worker
REDIS_HOST=localhost DELTA_LAKE_PATH=/data/delta python -m src.workers.stage2_worker
```

## Architecture

### Pipeline Flow

```
Seed URLs → Stage 1 (Discovery) → Stage 2 (Analysis) →
Stage 3 (Summarization) → Stage 4 (Advanced) → Final Output
```

### Component Overview

#### Stage 1: Discovery
- **ScoutSpider**: Rapid breadth-first discovery
- **DepthSpider**: Depth-first focused crawling
- **JSSpider**: JavaScript-rendered content extraction
- **Output**: `seed_urls`, `js_spider_queue`, `stage2_queue`

#### Stage 2: Content Analysis
- Validates and analyzes discovered content
- Filters and classifies pages
- Extracts structured data with validation
- **Output**: Validated content for Stage 3

#### Stage 3: Summarization
- Entity grouping and recency-weighted aggregation
- LLM-based summarization
- Temporal relevance scoring
- **Output**: Entity summaries

#### Stage 4: Advanced Processing
- ML-based classification
- Relationship extraction
- Final enrichment
- **Output**: Enriched final data

**Large-document worker input (#611).** `Stage4Worker` reads its work from the
`stage4_large_docs` queue that Stage 2 fills (`_route_to_stage4` for >50k-word
pages, `_route_pdf_to_stage4` for PDFs with `is_pdf=true`). A URL is pending
until one of its rows leaves `pending` or it already has a row in
`stage4_large_doc_summaries`. After each run, rows are updated with a row-level
Delta MERGE on `url`:

| `status` | meaning |
|---|---|
| `completed` | summary written to `stage4_large_doc_summaries` |
| `skipped:no_text` | fetch/extraction returned no text (e.g. image-only PDF) |
| `skipped:no_summary` | text extracted but the summarizer produced nothing |
| `pending` | not yet processed, or a transient fetch/summary-write failure (retried next run) |

Fallback: `is_massive_doc` rows in `stage2_page_analysis` that never reached
the queue are still summarized (PDF detected by `.pdf` URL suffix) and deduped
via the summaries table. The queue stays the source of truth.

**PDF budgets (#445).** PDF text extraction runs in a child process
(`src/stage4/pdf_extract_child.py`), so a pathological PDF can only kill the
child, never the Stage 4 worker:

| Env | Default | Effect |
|---|---|---|
| `STAGE4_PDF_MAX_BYTES` | 50 MiB | larger PDFs are rejected before extraction (`quarantined:too_large`) |
| `STAGE4_PDF_MAX_RSS_MB` | 1024 | child address-space cap (RLIMIT_AS); exceeding it, or a SIGKILL/SIGABRT/SIGSEGV, gives `quarantined:oom` |
| `STAGE4_PDF_TIMEOUT_S` | 120 | child killed after this many seconds (`quarantined:timeout`) |
| `STAGE4_PDF_MAX_PAGES` | 2000 | pages extracted per PDF |

An unreadable PDF becomes `quarantined:parse_error`. Quarantined rows are
settled: they're not retried (by tenacity or the next run), so a bad PDF
can't requeue forever. If no PDF library is installed in the image, the error
is transient and rows stay `pending`.

Metrics: `stage4_ocr_oom_total`, `stage4_ocr_timeout_total`,
`stage4_pdf_quarantined_total{reason}`. Keep the pod memory limit above
`STAGE4_PDF_MAX_RSS_MB` plus the summarizer model's footprint, so the child
budget trips before the cgroup OOM killer.

The extractor uses `pypdf` if installed, else `PyPDF2` (the pinned
dependency). Before this change, the code imported only `pypdf`, so every PDF
silently extracted to empty text.

### Type Safety (Phase 6)

All data is validated using Pydantic models:

```python
from src.core.models import URLRecord, Stage2Analysis

# Type-safe URL record
url_record = URLRecord(
    url="https://example.com",
    url_hash="abc123..."
)

# Validated analysis data
analysis = Stage2Analysis(
    url="https://example.com",
    url_hash="abc123...",
    word_count=500,
    quality_score=0.85,
    processed_at=datetime.now()
)
```

### Error Handling (Phase 7)

Robust error handling with retry and circuit breaker:

```python
from src.utils.retry import with_retry, CircuitBreaker
from src.core.exceptions import NetworkError

# Automatic retry with exponential backoff
@with_retry(max_attempts=3, base_delay=1.0)
async def fetch_url(url: str):
    # Your code here
    pass

# Circuit breaker for fault tolerance
cb = CircuitBreaker(failure_threshold=5, name="api")

@with_retry(circuit_breaker=cb)
async def call_api():
    # API call
    pass
```

### Performance Optimization (Phase 8)

Built-in caching and profiling:

```python
from src.utils.cache import cached
from src.utils.profiler import profile

# Cache expensive function results
@cached(ttl=3600)
async def expensive_operation(key: str):
    # Expensive computation
    return result

# Profile execution time
@profile
async def process_data(data):
    # Processing logic
    pass
```

## Configuration

### Canonical config file

Edit [`config.yml`](config.yml) — the single source of truth loaded by
`src.core.config.get_config()` and used by Scrapy settings (`src/settings.py`).
Do not rely on `config/{ENV}.yml` (not present, and not loaded). `CONFIG_ENV`
is not read. Dev, stage, and prod diverge through environment variables; the
checklist is [`docs/guides/ENV_DIVERGENCE.md`](docs/guides/ENV_DIVERGENCE.md)
(#788). `ENV` / `APP_ENV` only tighten the destructive-operation guard.

### Config reload semantics

`src/core/config.py` keeps the live configuration as an immutable, versioned snapshot (#590):

- `load()`, `reload()` and `set()` build a complete new snapshot and swap it in atomically. A reader sees the old generation or the new one, never a mix, such as a new Redis host with the old password.
- Each swap bumps `Config.generation`, exported as the `scrapy_config_generation` gauge.
- When several keys have to agree, read them from one snapshot: `snap = get_config().snapshot(); snap.get("redis.host"); snap.get("redis.password")`. Separate `get()` calls may straddle a reload.
- A reload that can't parse the file (half-written, not a mapping, or missing) keeps the previous snapshot, returns `False`, and increments `scrapy_config_reload_failures_total`. Only the very first load falls back to built-in defaults.
- `get()`, `get_section()` and `get_raw_config()` return copies; mutating them doesn't change the live config. Use `set()`.
- Write config files atomically (write a temp file, then `os.replace`) so a reload never sees a partial file.

### Metrics dual-export (Prometheus + Postgres)

Stage workers report batch throughput and per-URL errors through `src/utils/metrics_sink.py` (#586):

1. **Prometheus, always:** `scrapy_stage_urls_processed_total`, `scrapy_stage_batch_seconds_total`, `scrapy_stage_errors_total{stage,error_type}`. These are updated whether or not Postgres is configured or healthy, and are the source of truth for alerting.
2. **Postgres, best effort:** the same record goes to `performance_metrics` / `error_logs` for history.
3. **Postgres failure:** never raised into the crawl and never silently dropped. `scrapy_pg_metrics_writes_total{kind,outcome="failure"}` is incremented, the record is logged at WARNING as `metrics_sink_fallback kind=... payload={json}`, and the `PostgresMetricsSinkFailing` alert fires above a 10% failure rate for 5m. With Postgres disabled (no `DB_PASSWORD`), writes are counted as `outcome="disabled"`.

### Redis connection contract

Every process reads the same variables (#511):

| Env | Default | Meaning |
|---|---|---|
| `REDIS_HOST` | `localhost` | Redis hostname (Compose: `redis`; Helm non-prod: `<release>-redis`; Helm production: `redis.external.host`, see `k8s/README.md`) |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_PASSWORD` | unset | AUTH password (#184); Helm injects it from the `redis-credentials` Secret |
| `REDIS_DB` | `0` | Database index |

Compose, Helm, the container entrypoints, the Python services
(`src/utils/redis_env.py`) and kafka-delta-ingest (`redis_url_from_env` in
`src/main.rs`) all build the connection from these. `REDIS_URL`
(`redis://[:password@]host:port/db`) is accepted only as a fallback for
whatever the variables above leave unset. If `REDIS_HOST`/`REDIS_PORT` and
`REDIS_URL` name different servers, `REDIS_HOST`/`REDIS_PORT` win and a
warning is logged. Set only the four variables. Helm production sets
`REDIS_HOST` from `redis.external.host` (`k8s/README.md`, #216). The chart's
single replica is not HA: AOF can recover data after the node returns, and it
does not keep the queue available during node loss.

### Redis connection pool sizing (capacity model)

Each process's `RedisHelper` uses a bounded `redis.BlockingConnectionPool` (#533):

| Env | Default | Meaning |
|---|---|---|
| `REDIS_MAX_CONNECTIONS` | `50` | Max pooled connections per process |
| `REDIS_POOL_TIMEOUT` | `2.0` | Seconds to wait for a free connection before giving up |

- **On exhaustion,** the seen/claim paths **fail closed**. `SeenStoreUnavailable` is raised, admission pauses, and `redis_pool_exhausted_total{op}` is incremented. A timed-out claim is never treated as "unseen", so a burst can't become a duplicate-crawl storm. The `RedisPoolExhausted` and `RedisSeenStoreFailingClosed` alerts cover this.
- **Sizing rule:** `sum over processes (REDIS_MAX_CONNECTIONS) <= 0.8 × Redis maxclients`, where Redis defaults to `maxclients 10000`. The 20% is headroom for exporters, admin and failover. Example: 40 worker pods × 2 processes × 50 = 4,000 connections, which fits comfortably.
- **Per process,** a pool larger than the process's real concurrency (threads plus in-flight async tasks touching Redis) only wastes Redis memory. Size it to the concurrency, then check the inequality above.
- **Diagnosing:** a steady `redis_pool_exhausted_total` rate with healthy Redis latency means the pool is too small for the worker's concurrency. If it comes with high latency (`SLOWLOG`, CPU), fix Redis first, because a larger pool only queues more work on a slow server.

### Deployment profiles: core vs streaming

`docker/entrypoints/crawler-entrypoint.sh` (#615) chooses its profile from the environment:

| Profile | Where | Kafka | Entrypoint behaviour |
|---|---|---|---|
| **core** | `Scraping_project/docker-compose.yml` (Redis + stage workers) | none | `KAFKA_BOOTSTRAP_SERVERS` unset, so the Kafka wait is skipped |
| **streaming** | Helm chart (`application-configmap` sets `KAFKA_BOOTSTRAP_SERVERS`) | yes | waits for the first broker in the list (`host:port`; a `PLAINTEXT://` scheme is fine) |

- Both waits are bounded: `REDIS_WAIT_TIMEOUT` and `KAFKA_WAIT_TIMEOUT` each default to 120 s. When a dependency is unreachable, the container exits 1 (visible as restarts or CrashLoopBackOff) instead of looping forever.
- `REQUIRE_KAFKA=1` makes a missing `KAFKA_BOOTSTRAP_SERVERS` a hard error, for streaming deployments that must not silently fall back to core.

### robots.txt, Crawl-delay and Retry-After

Stage 1 obeys robots.txt by default (#186). `ROBOTSTXT_OBEY` is on in `src/settings.py` and in every spider's settings from `get_spider_settings()`. `PoliteRobotsTxtMiddleware` (`src/stage1/middlewares/robots_middleware.py`) replaces Scrapy's `RobotsTxtMiddleware` at the same priority (100):

- **Disallow.** robots.txt is fetched once per host and cached. Disallowed URLs are never requested. They are counted in `scrapy_robots_forbidden_total{spider}` and in crawl stats `robotstxt/forbidden`, and the first refusal per host is logged at INFO.
- **Crawl-delay** (#188). The host's download-slot delay is raised to the `Crawl-delay` for our user agent, capped at `ROBOTS_MAX_CRAWL_DELAY` (60s). The delay is re-applied before each request, so AutoThrottle can't drop below it. Hosts affected are counted in `scrapy_robots_crawl_delay_hosts_total{spider}`.
- **Missing robots.txt** (404) or a fetch error allows everything, matching Scrapy's behaviour.
- **Retry-After** (#188). On a 429 or 503 with `Retry-After` (seconds or an HTTP date), `RetryAfterMiddleware` (priority 560, so it runs ahead of Scrapy's RetryMiddleware) raises that host's download delay to the wait, capped at `RETRY_AFTER_MAX_DELAY` (120s). The retry and every other request to the host wait at least that long, and the old delay is restored afterwards. Waits are counted in `scrapy_retry_after_waits_total{spider}` and `scrapy_retry_after_seconds_total{spider}`.
- **Opting out** is only for sites you own: `ROBOTSTXT_OBEY=false`, `scrapy.robotstxt_obey: false`, or `stage1.spiders.<name>.robotstxt_obey: false`.

### Rate limits and per-domain concurrency

These are the production politeness defaults (#194). Scrapy's "domain" slot is the **hostname**, so each `*.uconn.edu` subdomain is throttled on its own.

| Setting | scout | deep_dive | Notes |
|---|---|---|---|
| `concurrent_requests` (global) | 1024 | 32 | spread across hosts |
| `concurrent_requests_per_domain` | 16 | 8 | hard per-host cap |
| `autothrottle_target_concurrency` | 8 | 2 | per host; clamped to the per-domain cap |
| `autothrottle_max_delay` | 60s | 60s | floored at 30s with a warning |

- **AutoThrottle** can only slow a host down to `AUTOTHROTTLE_MAX_DELAY`. The old 1–1.5s caps could not absorb a 429 storm, so `polite_autothrottle_max_delay()` (`src/stage1/middlewares/spider_config.py`) never lets it fall below 30s. `polite_target_concurrency()` clamps the target to the per-domain cap; the scout profile used to have 2048 against 512, which meant AutoThrottle never slowed anything down.
- **429/503 without Retry-After.** `RetryAfterMiddleware` backs the host off exponentially. The delay doubles on each consecutive one, starting at `RATE_LIMIT_BACKOFF_MIN` (1s) and going up to `RATE_LIMIT_BACKOFF_MAX` (defaults to the AutoThrottle max), and is held for `RATE_LIMIT_COOLDOWN_FACTOR` (4) times the delay before the old delay is restored. These are counted in `scrapy_rate_limit_backoffs_total{spider}` and in crawl stats `rate_limit/backoff_count` and `rate_limit/max_delay`.
- **Active waits survive AutoThrottle.** AutoThrottle recomputes the delay on every 200 response. While a Retry-After wait or a backoff is active, the enforced delay is re-applied on every request and response for that host.
- All of these can be overridden per spider under `stage1.spiders.<name>` in `config.yml` (`rate_limit_backoff_min`, `rate_limit_backoff_max`, `rate_limit_cooldown_factor`) or project-wide under `scrapy.*`.

### Egress proxy rotation

Stage 1 does not use a proxy unless you configure one (#198). `ProxyRotationMiddleware` (priority 745, just before Scrapy's `HttpProxyMiddleware`) is installed on the project and on every spider from `get_spider_settings()`, and it does nothing until a list is set. A non-empty list turns it on; you do not edit code.

- **Config.** `stage1.proxy.urls` (list of `http://` or `https://` proxies, or `host:port`) or `scrapy.proxy_list`. The `scrapy:` section wins when both are set. `stage1.proxy.enabled: false` or `scrapy.proxy_rotation_enabled: false` is a kill switch.
- **Environment.** `PROXY_LIST` (comma-separated) overrides the file. `PROXY_ROTATION_ENABLED=0` forces rotation off. `PROXY_QUARANTINE_SECONDS` (default 300) and `PROXY_MAX_ROTATIONS` (default 3 extra attempts per request) override `stage1.proxy.quarantine_seconds` and `stage1.proxy.max_rotations`.
- **Rotation.** Requests round-robin through healthy proxies. HTTP 407, HTTP 429, or a download timeout quarantines that proxy and retries on the next healthy one. Credentials in the proxy URL are sent to the proxy but never logged or used as a metric label.
- **No healthy proxy.** The download is dropped (`IgnoreRequest`) instead of going out from the crawler's own IP. It resumes when a quarantine expires.
- **Metrics.** `scrapy_proxy_failures_total{spider,reason}` and crawl stats `proxy_rotation/failures`. `scrapy_proxy_quarantined{spider}` is how many proxies are in quarantine.

### Soft-ban / captcha guard

Challenge and captcha pages are not content (#582). `src/utils/soft_ban.py`
classifies responses:

- **HTTP 429** is always a soft ban.
- **`cf-mitigated: challenge`** header means a Cloudflare challenge, at any status.
- **403/503** count only if the body matches a signature (a plain 403 stays a normal, terminal HTTP error).
- **200** counts only if a signature matches *and* the page has fewer than `SOFT_BAN_MAX_WORDS` (400) visible words, so articles that merely mention captchas pass.

Built-in signatures: Cloudflare, reCAPTCHA, hCaptcha, PerimeterX, DataDome,
Akamai "Access Denied", and generic bot-check text. To extend or override
them, set `SOFT_BAN_SIGNATURES='{"name": "regex"}'`; an empty regex disables
a built-in.

| Where | What happens |
|---|---|
| Stage 2 | Row quarantined to `stage2_errors` with `error_message = soft_ban:<signature>`; never written to `stage2_page_analysis`; queue row stays `pending` (retried, then DLQ after `STAGE2_MAX_RETRIES`). |
| Stage 1 | `SoftBanMiddleware` (priority 540, after retries) drops the response with `IgnoreRequest`, so no links are followed. |
| Domain backoff | `SOFT_BAN_BACKOFF_THRESHOLD` (3) soft bans within `SOFT_BAN_BACKOFF_WINDOW` (60s) put the domain into cooldown for `SOFT_BAN_BACKOFF_COOLDOWN` (300s). Stage 2 defers that domain's URLs (left `pending`, not counted as failures); Stage 1 raises the domain's download delay to `SOFT_BAN_SLOT_DELAY` (30s) and restores it afterwards. |

Metrics: `scrapy_soft_ban_total{stage,signature}`,
`scrapy_soft_ban_domain_backoff_total{stage}`, `scrapy_soft_ban_deferred_total{stage}`.
Alert: `ScrapySoftBanSpike`. Fixture pages live in `tests/fixtures/soft_ban/`.

### Kafka producer: keys and delivery semantics

- **Keyed by `url_hash`** (#285). `KafkaPipeline` sends each record with key = `url_hash`. If an item has no `url_hash`, the key is derived from `url` with the lake hasher (`seed_manager.default_url_hasher`). Every version of a URL therefore lands on the same partition, in order, and consumers can dedupe or upsert per partition. Set `kafka.message_key_field` / `KAFKA_MESSAGE_KEY_FIELD` to key by another field, or `""` for unkeyed.
- **Idempotent producer** (#174, #464). The defaults are `acks=all`, `enable.idempotence=true`, and at most 5 in-flight requests, so librdkafka retries never duplicate or reorder messages within a partition. With `KAFKA_REQUIRE_IDEMPOTENCE=true` (`kafka.require_idempotence`), the producer **refuses to start** if any override (env `KAFKA_PRODUCER_ACKS`, `config.yml kafka.producer`, `KAFKA_PRODUCER_CONFIG`) would break that guarantee. The Helm application configmap sets it to `true`. Locally it is off, so `KAFKA_PRODUCER_ACKS=1` still works.
- **What is exactly-once and what isn't.** The producer is exactly-once and in-order per partition within one producer session. End to end, delivery is **at-least-once**: a restarted producer or a crash between kafka-delta-ingest's Delta commit and its offset commit can replay messages. Lake tables dedupe by `url_hash`.

### Shared retry policy

Scrapy and Stage 2 use one policy (#215): `load_retry_policy()` in
`src/utils/retry.py`. Put a `retry:` section in `config.yml` to override the
profile defaults (every key is optional):

| Key | Meaning |
|---|---|
| `attempts` | Total tries, including the first request. Scrapy `RETRY_TIMES` is `attempts - 1`. Stage 2 in-request attempts use the same number. |
| `queue_attempts` | Stage 2 queue cap (`STAGE2_MAX_RETRIES`). Defaults to `attempts`. |
| `backoff_base` / `backoff_max` | Stage 2 exponential backoff, in seconds. |
| `retryable_statuses` | HTTP statuses worth retrying. 429 and 503 are always added back if omitted. |

When a key is absent:

| Profile | How `ENV` is set | Total attempts | Scrapy `RETRY_TIMES` | Stage 2 backoff |
|---|---|---|---|---|
| development | unset, `development`, or anything else | 3 | 2, or the spider's own `retry_times` | 0.5s base, 8s cap |
| production | `production` or `prod` (`APP_ENV` counts; Helm sets `ENV=production`) | 5 | at least 4; a lower per-spider `retry_times` is raised | 1s base, 30s cap |

`retry.attempts` replaces the per-spider `retry_times` in both profiles. In
development, with no `retry` section, an explicit `scrapy.retry_times` or
`stage1.spiders.<name>.retry_times` still wins. 429 is retryable on the Scrapy
side and on the Stage 2 queue; Stage 2 does not retry it inside the same fetch
(it is a soft ban, #582). A plain 503 is retried in-request. `STAGE2_HTTP_ATTEMPTS`,
`STAGE2_MAX_RETRIES`, `STAGE2_HTTP_BACKOFF_BASE` and `STAGE2_HTTP_BACKOFF_MAX`
still override Stage 2.

### Stage 2 HTTP retries and circuit breaker

Stage 2 retries transient fetch failures inside the same run (#158), so a
single blip doesn't cost a whole queue cycle. The numbers below are the
development defaults; production uses the shared policy above:

| Failure | Behaviour |
|---|---|
| Timeout, connection/payload error, HTTP 408/500/502/503/504 | Retried up to `STAGE2_HTTP_ATTEMPTS` (3) total attempts with exponential backoff and jitter: `STAGE2_HTTP_BACKOFF_BASE` (0.5s) × 2^(attempt−1), capped at `STAGE2_HTTP_BACKOFF_MAX` (8s). A numeric `Retry-After` header raises the delay (still capped). |
| Soft ban (429, challenge pages) | Not retried in-request; handled by the soft-ban guard above. |
| Other 4xx (400/401/404/410, …) | Fail fast: one request, terminal error. |
| Every attempt failed | Error record to `stage2_errors`; the queue row stays `pending` and is retried next run, then sent to the DLQ after `STAGE2_MAX_RETRIES`. |

Each host has its own circuit breaker. After `STAGE2_BREAKER_FAILURES` (5)
URLs on one host exhaust their attempts, the breaker opens for
`STAGE2_BREAKER_RECOVERY` (60s). While it is open, that host's URLs are
deferred (left `pending`, not counted as failures) instead of being fetched.

Metrics: `stage2_http_fetches_total{outcome=first_try|recovered|exhausted|circuit_open}`
and `stage2_http_retries_total{reason}`. Each retry and each recovery is logged
with its attempt number.

### URL canonicalization

Every URL touchpoint uses `src/utils/url_canon.py` (#728): Redis seen/claim
set members, `QueueItemPipeline` rows (`stage2_queue`, `js_spider_queue`),
`SeedManager`, Stage 2 `url_hash`, `URLProcessor` and
`validation.normalize_url`. Canonical form:
- lowercase scheme, host and path;
- no default port and no fragment;
- trailing slash stripped (except the root);
- `utm_*` and other tracking parameters removed, remaining params sorted.

`url_hash` = sha256(canonical URL)[:16]. The rules match what Stage 1 already
hashed with, so existing scout hashes are unchanged; `js_spider` hashes and
raw-URL SeedManager hashes now converge on the same value.

### Docker build context

`.dockerignore` comments every exclusion (#625). Runtime data (`data/`,
`logs/`), caches, VCS/CI metadata, docs, Kubernetes manifests and Rust
`target/` stay out of the context. `Dockerfile`, `docker-compose.yml` and
`monitoring/` stay in. Compose still **bind-mounts**
`monitoring/prometheus.yml` and `monitoring/alerting/` at runtime, so config
edits apply without a rebuild; the copy inside the image is the default and
can be baked in when wanted.

### Memory soft-stop vs. container limits

A cgroup OOM kill gives Twisted no chance to run `close_spider`, so batched
queue rows and the Kafka producer queue would be lost (#539).
`src.memory_soft_stop.MemorySoftStop` (enabled in `EXTENSIONS`, including
orchestrator runs) reads the container's limit and usage from cgroup v2
(`memory.max` / `memory.current`) or v1. When usage reaches
`MEMORY_SOFT_STOP_FRACTION` (0.85) of the limit, it calls
`engine.close_spider(spider, "memory_soft_stop")`: no new requests, in-flight
responses finish, and every pipeline's `close_spider` flushes. The Kafka
pipeline spills anything undelivered to its fsync'd spill file.

- **Kubernetes/compose:** the soft stop keys off `resources.limits.memory` /
  `deploy.resources.limits.memory` automatically. Leave ~15% headroom: the
  drain itself (Kafka flush, final Delta batch) needs memory. Lower the
  fraction for spiders with large batches.
- **No visible limit** (bare metal, `memory.max = max`): the extension
  disables itself unless `MEMORY_SOFT_STOP_LIMIT_MB` is set.
- Scrapy's `MEMUSAGE_LIMIT_MB` is still set but compares *peak* RSS with a
  fixed number. Treat it as a backstop, not the container guard.
- **Observability:** `scrapy_memory_soft_stop_total`, `scrapy_memory_usage_ratio`,
  crawl stat `memory_soft_stop/triggered`, finish reason `memory_soft_stop`.
  A soft-stopped crawl exits cleanly; the next scheduled run resumes from
  the queues.

### Environment Variables

```bash
# Redis (#511: the only contract; REDIS_URL is a fallback, not needed)
REDIS_HOST=redis-service
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=0

# Delta Lake
DELTA_LAKE_PATH=/data/delta

# Logging
LOG_LEVEL=INFO

# Workers
WORKERS=4
CONCURRENCY=10

# Continuous Stage 2/3 workers (override config.yml stage2/stage3 max_workers + batch_size)
STAGE2_CONCURRENT=100
STAGE2_BATCH_SIZE=50
STAGE3_CONCURRENT=50
STAGE3_BATCH_SIZE=100
```

Continuous Stage 2/3 workers resolve concurrency and batch size from
`STAGE{N}_CONCURRENT` / `STAGE{N}_BATCH_SIZE`, then `config.yml`
`stage{N}.max_workers` / `stage{N}.batch_size`, and finally built-in defaults
(see `src.core.config.stage_worker_settings`).

### Docker Configuration

Edit `docker-compose.yml` to customize:
- Worker replicas
- Memory limits
- CPU allocation
- Port mappings

### Kubernetes Configuration

Configure the Helm chart via `k8s/helm/scraping-pipeline/values.yaml` (supported). Alternatively, edit `k8s/deployment.yaml` (minimal quick-start) for:
- Auto-scaling policies
- Resource requests/limits
- Persistent volume sizes
- Service configuration

### SSRF guard (#682)

Every URL is checked by `src/utils/ssrf.py` **before** any request is made or a queue row is written. The checks run at three points:

- **Stage 1 (Scrapy):** `SSRFGuardMiddleware` is the first downloader middleware. It is registered in `src/settings.py` and in `spider_config`, and it raises `IgnoreRequest("ssrf_blocked:<reason>")`. Scrapy redirects re-enter the middleware chain, so every hop is checked.
- **Queueing:** `QueueItemPipeline` never writes an SSRF-like URL to `stage2_queue` or `js_spider_queue`.
- **Stage 2 (aiohttp):** redirects are followed by hand, with each hop checked before it connects. A refusal is a terminal `ssrf_blocked:<reason>` error and is not retried.

What gets blocked:
- non-http(s) schemes and embedded credentials;
- loopback, private, link-local (including `169.254.169.254` metadata), CGNAT, multicast and unspecified IPs, in every spelling: decimal `2130706433`, hex `0x7f000001`, octal `0177.0.0.1`, short `127.1`, IPv6 `[::1]`, IPv4-mapped `[::ffff:127.0.0.1]`, and zone IDs;
- `localhost` aliases and `*.localhost` / `*.internal` / `*.local`;
- single-label names such as `redis` or `kafka` (in-cluster services).

Settings and env:
- `SSRF_GUARD_ENABLED` (default on).
- `SSRF_RESOLVE_DNS=1` additionally rejects hostnames whose DNS answers are non-global. It's off by default because it blocks the reactor; `OffsiteMiddleware` already limits crawls to `allowed_domains`.
- `SSRF_ALLOWED_HOSTS=127.0.0.1,10.0.0.0/8` is an explicit allowlist, for example for local fixture servers.

Metric: `scrapy_ssrf_blocked_total{stage="stage1|stage2|queue", reason}`.

### Redis memory policy: durable keys vs TTL keys (#161)

Redis holds durable crawl state, and losing it is not a cache miss:

| Keys | TTL | If lost |
|---|---|---|
| `site:{site_id}:{prefix}:urls` seen-sets (dedup/claims), e.g. `site:uconn:seen:urls` | none | every URL looks new, so the crawl starts over |
| Stage queues and the priority queue | none | queued work disappears |
| `depth_spider:last_crawl:*` and other cache-like keys | yes (`ex=`) | recomputed |

Compose, `k8s/deployment.yaml` and Helm (`redis.config.maxmemoryPolicy`) run `--maxmemory-policy volatile-lru`, so only keys with a TTL can be evicted. When Redis reaches `maxmemory` with nothing evictable left, it rejects writes (`OOM command not allowed`) instead of silently dropping a seen set. The seen store then fails closed (see `RedisSeenStoreFailingClosed`).

- **Never use an `allkeys-*` policy.** `RedisHelper` logs an ERROR at connect time if the server runs one.
- **New cache-like keys must set a TTL.** Durable keys must not.
- **Memory SLO.** Stay under 80% of `maxmemory`. The `RedisHighMemory` alert fires above that for 5 minutes. Raise `maxmemory` or drain the queues before writes start failing.

Seen-sets are namespaced by site or tenant (#191). `RedisHelper` writes only `site:{site_id}:{prefix}:urls` (for example `site:uconn:seen:urls`). The id is the `site_id` argument, else `SITE_ID`, else `TENANT_ID`, else `REDIS_SITE_ID`, else `default`. Two crawl profiles must not share an id. Unscoped keys from older releases (`seen:urls`, `scout:urls`, `{prefix}:urls`) are not read. There is no automatic migration: before deploying, `RENAME seen:urls site:default:seen:urls` (and the same for every other prefix) only when the new key is absent. Details are in `SEEN_KEY_MIGRATION` in `src/utils/redis.py`. These variables are not part of the Redis connection contract and Compose/Helm do not set `REDIS_URL`.

### TLS certificate verification (#584)

Every outbound HTTPS request verifies the server certificate. aiohttp, httpx and requests verify by default. The Scrapy downloader uses `BrowserLikeContextFactory` (set in `src/settings.py` from `src/core/tls_policy.py`) instead of Scrapy's default factory, which accepts any certificate. `tests/unit/test_tls_policy.py` fails CI if code adds `verify=False`, `ssl=False`, `CERT_NONE` or similar bypasses. It also proves end to end that a self-signed server is rejected.

**Exception process.** For a site with a broken chain, fix trust (install the issuing CA on the host or image) rather than disabling checks. As a temporary last resort, set `SCRAPY_TLS_INSECURE=1` for that run. It is logged at ERROR on startup and exported as `scrapy_tls_verification_disabled 1`, so it shows up in monitoring.

### Kafka topics and validation failures (#410)

`config.yml` `kafka.topics` lists every topic the pipeline produces to, and `kafka.topic_settings` sets partitions and retention for each one:

| Logical name | Default topic | Producer | Retention |
|---|---|---|---|
| `scraped_items` | `scraped-items` | `KafkaPipeline` (Stage 1 items for kafka-delta-ingest) | 7 days |
| `dead_letter` | `scraped-items-dlq` | Dead-letter queue | 30 days |
| `validation_failures` | `validation_failures` | `SchemaValidationPipeline` | 14 days |

`python -m src.utils.kafka_topics` creates the missing topics with those settings and never alters topics that already exist. `--dry-run` prints the plan without connecting. It reads `KAFKA_BOOTSTRAP_SERVERS` and the `KAFKA_SASL_*` variables. The Helm chart runs the same command as a post-install/upgrade hook Job (`kafka.topicsJob`). Scrapy's `VALIDATION_FAILURES_TOPIC` setting comes from `kafka.topics.validation_failures`.

**Triage.** Each message on `validation_failures` is one `ValidationFailureRecord` (`src/schemas.py`) in JSON. The fields are `url`, `field_name`, `violation_rule`, `attempted_value`, `error_message`, `spider_name`, `failed_at_utc` and `pipeline_version`. The item itself was dropped. Read the topic with any consumer, for example:

```bash
kafka-console-consumer --bootstrap-server "$KAFKA_BOOTSTRAP_SERVERS" \
  --topic validation_failures --from-beginning --group validation-triage
```

Group the records by `field_name` and `violation_rule` to find the spider or schema rule that is rejecting items. Fix the cause, then recrawl the affected URLs. They are not replayed automatically.

## Testing

### Run All Tests

```bash
# Run test suite
pytest

# With coverage
pytest --cov=src --cov-report=html

# Verbose output
pytest -v
```

### Run Specific Test Suites

```bash
# Cache tests
pytest tests/test_cache.py -v

# Model validation tests
pytest tests/test_models.py -v

# Retry and circuit breaker tests
pytest tests/test_retry.py -v
```

### rustmapper JSONL contract (#936)

Fixtures: [tests/fixtures/rustmapper_seed/](tests/fixtures/rustmapper_seed/).
`accept.jsonl` is what SeedManager inserts (`source_spider=rustmapper`).
`reject.jsonl` is the same six-field shape, but SeedManager rejects the URL.
`incompatible.jsonl` (unknown field, missing field, or wrong JSON type) fails
`tests/contract/test_rustmapper_seed_contract.py` and `cargo test` in
`rustmapper_contract/`. CI runs both sides
(`.github/workflows/ci-rustmapper-contract.yml`). The pytest also runs in the
default suite.

### Test Results

Current test status:
- **25 tests passing** ✓
- Cache layer: 6/6 tests passing
- Model validation: 10/10 tests passing
- Retry/circuit breaker: 9/9 tests passing

## Monitoring

### Prometheus Metrics

Available at `http://localhost:9090`:

- `pipeline_errors_total{stage,error_type,site_id}`: Total pipeline errors (metrics_exporter.py `errors.total`, named by `statsd_mapping.yml`). `site_id` is the configured site id (#192).
- `redis_queue_length{queue,site_id}`: Pending items per queue (metrics_exporter.py `redis.queue.length`)
- `redis_queue_backpressure_total{queue,action}`: Producer block or shed events when a Redis queue is over its high watermark (`REDIS_QUEUE_HIGH_WATERMARK`, default 10000, the same line `QueueBacklog` uses)
- `cache_hits_total`: Cache hit count
- `cache_misses_total`: Cache miss count
- `retry_attempts_total`: Retry attempts
- `circuit_breaker_state{name,host}`: Circuit breaker state (0=closed, 1=open, 2=half-open). `metrics_exporter.py` sends `circuit_breaker.state`; statsd-exporter maps the name. Shared across replicas via Redis unless `CIRCUIT_BREAKER_SHARED=0`.

Every queue worker (Stage 2/3/4) serves its own registry on
`WORKER_METRICS_PORT` (default 9430, `WORKER_METRICS_ENABLED=0` to disable),
so worker-side counters (soft bans, deferrals, recency outcomes, ...) reach
Prometheus as `stage{2,3,4}_worker` jobs, one target per replica, with
`scrapy_worker_up{component}` as the liveness series. Scrape topology for Helm:
[k8s/README.md](k8s/README.md#what-gets-scraped-789).

### Grafana Dashboards

Access at `http://localhost:3000` (admin/admin):

- **Pipeline Overview**: End-to-end metrics
- **Worker Performance**: Per-worker statistics
- **Cache Performance**: Hit rates and latency
- **Error Rates**: Error tracking and alerting

### Health Checks

```bash
# Container health (Docker HEALTHCHECK = `python -m src.utils.probe container`:
# main process alive, Redis answers PING when REDIS_HOST is set, plus any
# HEALTHCHECK_TCP=host:port dependencies). Workers expose no HTTP /health route.
docker inspect --format '{{.State.Health.Status}}' <container>
docker compose exec stage3-worker python -m src.utils.probe container && echo healthy

# Check Redis
redis-cli ping

# Check Prometheus
curl http://localhost:9090/-/healthy
```

## Deployment

### Local Development

```bash
python start.py
```

Same as Quick Start: profiles `core` and `monitoring`. See [COMPOSE.md](COMPOSE.md) and [docs/guides/RUNNING.md](docs/guides/RUNNING.md). A bare `docker compose up -d` starts no services.

### Observability

`docker-compose.observability.yml` is two things. A bare `up` starts the packaged Prometheus, Grafana, and StatsD exporter (loopback `9095`, `3001`, `9102`/`9125`) with the lag / Redis depth / crawl rate / Delta commits dashboard. Loki, Jaeger, and the OpenTelemetry collector stay on the `observability` profile and still overlay `docker-compose.yml` (same `scraper-network`). There is no separate production compose file.

```bash
# Pack alone (clean host)
docker compose -f docker-compose.observability.yml up -d

# App stack plus tracing. The pack containers start too (different ports).
docker compose -f docker-compose.yml -f docker-compose.observability.yml --profile core --profile monitoring --profile observability up -d
```

Control Center is http://localhost:8080. It does not embed Grafana. Deep-link the pack board at http://127.0.0.1:3001/d/ops-lag-depth-commits (the same uid is on the monitoring-profile Grafana at http://localhost:3000/d/ops-lag-depth-commits). See [../MONITORING.md](../MONITORING.md) for OTEL traces and [dashboard/README.md](dashboard/README.md) for the Control Center side of the link.

### Release images

`.github/workflows/cd-release.yml` builds three targets from `Dockerfile` on `v*` tags
(and on pull requests that touch the image recipe, without pushing): `crawler`,
`metrics` and `kafka-delta-ingest`. Build one locally with
`docker build --target metrics -t scrapy-metrics .`.

### Production Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for comprehensive deployment guide including:
- Docker image building
- Kubernetes deployment
- Scaling strategies
- Backup procedures
- Security best practices

### CI/CD Pipeline

GitHub Actions workflow automatically:
1. Runs tests on PR and push
2. Performs type checking with mypy
3. Builds Docker images
4. Deploys to Kubernetes (on main branch)

Required secrets:
- `DOCKER_USERNAME`
- `DOCKER_PASSWORD`
- `KUBE_CONFIG`

## Data Storage

### Delta Lake Tables

All tables use PyArrow schemas for validation:

| Table | Description | Schema |
|-------|-------------|--------|
| `seed_urls` | Initial seed URLs | URLRecord |
| `discovered_urls` | All discovered URLs | URLRecord |
| `stage2_queue` | Pages for analysis | Stage2Analysis |
| `stage3_queue` | Summarization queue | Stage3Summary |
| `errors` | Error tracking | ErrorRecord |

`stage1_discovery` and `stage2_page_analysis` are partitioned by registrable domain. A row whose URL has no usable http(s) host (empty, `mailto:`, garbage) is not written under a catch-all `domain=unknown` partition. It goes to `domain_quarantine` (`source_table`, `url`, `reason`, `row_json`, `quarantined_at`), counted by `delta_unknown_domain_rows_total{table}`, and the `DeltaUndomainableRows` alert fires on a sustained stream (#458). For tables written before this change, `LakehouseManager.repair_unknown_domains(table)` reports what is in the legacy `unknown` partition. Pass `apply=True` to move repairable rows to their real domain and the rest to quarantine.

### Recency scoring contract (silver → gold)

`RecencyScoringPipeline` writes `recency_score` as exponential decay of the item's `publication_date` (`RECENCY_DECAY_CONSTANT`, default 0.01/day):

- **The score is in `[0.0, 1.0]`** when `publication_date` parses. 1.0 means published now.
- **The score is `null` when the date is missing or unparseable.** That means "freshness unknown", not median relevance. The pipeline no longer invents `0.5` (#675). To opt back into imputation, set `RECENCY_DEFAULT_SCORE` (or `scrapy.recency_default_score` in `config.yml`) to a float. Imputed items are still counted as missing.
- **Gold:** `entity_summaries.max_recency_score` is the highest *known* score among an entity's items. It is `null` when none of them has a date. Items without a score rank after scored ones.
- **Metric:** `scrapy_recency_items_total{outcome="scored|missing_date|unparseable_date"}`. Missing-date rate = `sum(rate(scrapy_recency_items_total{outcome!="scored"}[5m])) / sum(rate(scrapy_recency_items_total[5m]))`.

Consumers must handle `null` explicitly, for example by excluding those rows from freshness-weighted averages, rather than coalescing to a number.

### Schema Evolution Policy

Every append and overwrite reads the table's current schema from its `_delta_log`, not from process memory. So any number of workers or pods writing the same table agree on one schema.

- **Additive only.** Columns a batch carries that the table lacks are appended (`schema_mode="merge"`) and counted in `delta_schema_evolutions_total{table}`. A column that is null in every row of a batch is skipped until a real value arrives, because it carries no type.
- **Existing column types win.** Rows are cast to the table's types. Values that don't cast are quarantined to `cast_quarantine` (`delta_lake.cast_mode: strict`) or nulled (`coerce`), and counted in `delta_cast_failures_total`.
- **Required columns are never null-filled.** A row missing a non-nullable column is quarantined, in both modes. The rest of the batch is still written.
- **Overwrite replaces rows, not the schema (#509).** `mode="overwrite"` goes through the same cast and additive path and commits with `schema_mode="merge"`. Columns that other writers evolved survive, null in the new rows.
- **Breaking changes** (renames, type narrowing, dropping columns) are never implicit. Rewrite the table deliberately with `write(..., mode="overwrite", schema_overwrite=True)`. That write is always synchronous, logged as `[SCHEMA OVERWRITE]`, and counted in `delta_schema_overwrites_total{table}`.

### Bronze record contract (#227, #302)

Crawl items are validated once against `src/schemas.py` `BaseRecordSchema` (by `SchemaValidationPipeline`) and again by kafka-delta-ingest. Both enforce the same required list: `BRONZE_REQUIRED_FIELDS` in Python and `REQUIRED_INGEST_FIELDS` in `kafka-delta-ingest/src/main.rs`. `tests/unit/test_bronze_schema_contract.py` fails if they diverge.

| Field | Bronze | Notes |
|---|---|---|
| `url`, `scraped_at_utc`, `spider_name` | required | `scraped_at_utc` and `spider_name` are stamped before validation if the spider didn't set them |
| `source_url` | optional | defaults to `url` |
| `title`, `content`, `publication_date` | optional | a page with no extractable title or date is still crawl data |
| costs, `category_*`, `entity_id`, `recency_score` | optional | still validated when present (non-negative costs, totals add up, confidence in [0, 1]) |

Silver and analytics consumers that need `publication_date` filter on it or backfill it there. Ordering and partitioning fall back to `scraped_at_utc`; the Delta `date` partition is derived from it. Items accepted without a date are counted in `scrapy_items_missing_publication_date_total{spider}`. Drops are counted in `scrapy_schema_validation_drops_total{spider,field}` and published to `validation_failures`.

### Type-Safe Operations

```python
from src.utils.delta import DeltaLakeHelper
from src.core.models import Stage2Analysis

delta = DeltaLakeHelper()

# Write with validation
data = [Stage2Analysis(...)]
delta.write_typed("stage2_queue", data, Stage2Analysis)

# Read with validation
validated_data = delta.read_typed("stage2_queue", Stage2Analysis)
```

### Retention: tiers, preview, forensic hold

| What | Policy | Config (`delta_lake.*`) |
|---|---|---|
| Delta vacuum (old files after overwrite/delete) | Per-table tier; `default` 168h, `stage1_errors`/`stage2_errors` 720h (30 days of forensic history) (#632) | `table_retention_hours` |
| Completed/failed queue rows | Archived to `<table>_history`, then deleted after 168h (#754) | `queue_retention_hours`, `queue_gc_archive` |
| File DLQ (`data/dlq/*.json`) | Deleted after 30 days by the idle maintenance worker (#632) | `dlq_retention_days` (0 = keep) |
| `stage1_offsite_candidates` | Pending sightings dropped after 30 days, rejected ones 7 days after review; accepted kept (#878) | `offsite_pending_retention_days`, `offsite_rejected_retention_days` |

- **Preview before reclaiming (#602):** `python cli.py lake-vacuum [--table T] [--verbose]` lists what a vacuum would delete (files and bytes per table) and deletes nothing. Add `--apply` to reclaim. Tiers below 168h turn off the delta-rs retention safety check for that table, so set them deliberately.
- **Forensic hold:** to keep error evidence longer, raise that table's tier (e.g. `stage1_errors: 2160`) or set `dlq_retention_days: 0`. Nothing older than the tier can be restored by time travel once vacuumed.
- **Offsite review (#878):** run `python cli.py offsite list [--status pending]`, then `offsite accept URL…` or `offsite reject URL…`. `offsite promote` seeds accepted URLs once (idempotent MERGE; `promoted_at` stamped). `offsite gc [--dry-run]` applies the retention above, which the maintenance worker also runs. Gauge: `delta_offsite_candidate_urls{status}`.

## Project Structure

Paths are relative to `Scraping_project/`; run every command from there (#334).
`tests/unit/test_readme_layout.py` fails if this tree drifts from the disk (#489).

```
Scraping_project/
├── src/
│   ├── core/                    # config.py, models.py, schemas.py, exceptions.py
│   ├── common/                  # legacy managers (config, crawl data, seeds)
│   ├── lakehouse/               # lakehouse_manager.py, seed_manager.py
│   ├── utils/                   # delta.py, redis.py, retry.py, dead_letter_queue.py, cache.py
│   ├── stage1/                  # discovery
│   │   ├── scout_spider.py      # scrapy name: scout
│   │   ├── sitemap_parser.py
│   │   ├── middlewares/
│   │   ├── processors/
│   │   └── experimental/        # depth, javascript, deep_dive, base spiders
│   │       ├── depth_spider.py
│   │       ├── js_spider.py
│   │       ├── deep_dive_spider.py
│   │       └── base_spider.py
│   ├── stage2/                  # stage2_worker.py, intelligent_analyzer.py
│   ├── stage3/                  # stage3_worker.py
│   ├── stage4/                  # stage4_worker.py, large_doc_processor.py
│   ├── workers/                 # stage1_worker.py … stage4_worker.py entrypoints
│   ├── orchestrator/            # pipeline_orchestrator.py
│   ├── pipelines.py             # Scrapy item pipelines
│   └── settings.py              # Scrapy settings
├── tests/                       # unit/, integration/, component/, contract/, performance/
│   ├── conftest.py
│   └── README.md
├── monitoring/
│   ├── prometheus.yml
│   ├── alerting_rules.yml       # alerts (Helm ships an identical copy)
│   ├── recording_rules.yml
│   └── dashboards/              # Grafana dashboards (provisioned by compose/Helm)
├── k8s/
│   ├── deployment.yaml
│   └── helm/                    # scraping-pipeline chart
├── kafka-delta-ingest/          # Rust Kafka -> Delta ingestor
├── scripts/
├── start.py                     # local launcher (python start.py)
├── cli.py                       # seed/ops CLI (python cli.py --help)
├── scrapy.cfg
├── config.yml
├── docker-compose.yml
├── Dockerfile
├── Makefile
├── mypy.ini
├── pytest.ini
├── requirements.txt
├── dev-requirements.txt
└── DEPLOYMENT.md
```

### Spiders

Spiders are run by their scrapy **name**, not their file name (`scrapy crawl depth`,
not `scrapy crawl depth_spider`). `scrapy list` (from `Scraping_project/`) prints them:

| scrapy name | Module |
|---|---|
| `scout` | `src/stage1/scout_spider.py` |
| `depth` | `src/stage1/experimental/depth_spider.py` |
| `javascript` | `src/stage1/experimental/js_spider.py` |
| `deep_dive` | `src/stage1/experimental/deep_dive_spider.py` |
| `base` | `src/stage1/experimental/base_spider.py` |

## Development Evolution

This pipeline evolved through 10 major phases:

1. **Phase 1-3**: Initial code organization and file structure
2. **Phase 4-5**: Import updates and cleanup
3. **Phase 6**: Type safety with Pydantic and MyPy
4. **Phase 7**: Error handling and resilience patterns
5. **Phase 8**: Performance optimization and caching
6. **Phase 9**: Comprehensive testing infrastructure
7. **Phase 10**: Production deployment configurations

See `EVOLUTION_ROADMAP.md` for detailed phase documentation.

## Troubleshooting

### Common Issues

**Issue**: Tests failing with validation errors
```bash
# Solution: Check model definitions
pytest tests/test_models.py -v
```

**Issue**: Redis connection errors
```bash
# Solution: Check Redis is running
docker-compose ps redis
docker-compose restart redis
```

**Issue**: Circuit breaker open
```bash
# Solution: Check error logs and reset if needed
# Circuit breaker will auto-recover after timeout
```

**Issue**: Cache misses
```bash
# Solution: Check Redis memory and TTL settings
redis-cli INFO memory
```

### Debug Mode

Enable debug logging:
```bash
export LOG_LEVEL=DEBUG
python -m src.workers.stage2_worker
```

## Performance Tuning

### Worker Scaling

Recommended configuration:
- **Stage 1**: 1 instance (I/O bound)
- **Stage 2**: 2-5 instances (CPU bound)
- **Stage 3**: 1-2 instances (API limited)
- **Stage 4**: 1 instance (memory intensive)

### Cache Tuning

Adjust cache settings in `src/utils/cache.py`:
```python
cache = SmartCache(
    redis_client=redis,
    strategy=CacheStrategy.LRU,
    max_local_size=1000,    # L1 cache size
    default_ttl=3600        # 1 hour TTL
)
```

### Connection Pool

Configure in `src/utils/connection_pool.py`:
```python
pool = ConnectionPool(
    factory=create_connection,
    min_size=5,
    max_size=20,
    timeout=30.0
)
```

## Contributing

1. Create a feature branch
2. Write tests for new functionality
3. Ensure all tests pass: `pytest`
4. Run type checking: `mypy src/`
5. Submit a pull request

## License

[Your License Here]

## Contact

[Your Contact Information]

---

**Production Status**: ✓ Ready for deployment

Last Updated: 2025-11-09

## Happy path (Redis + one worker)

Minimal check that Docker entrypoints resolve after #142:

```bash
cd Scraping_project
docker compose up -d redis
docker compose up -d --no-deps stage2-worker
# or locally (with deps + Redis):
# python -m src.workers.stage2_worker
```

Default image CMD is `python -m src.main`. Compose stage workers use `python -m src.workers.stageN_worker`.
