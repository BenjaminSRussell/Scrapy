# UConn Web Scraping Pipeline

A production-grade, type-safe, resilient web scraping pipeline designed for large-scale institutional data collection and analysis.

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

### Prerequisites
- Docker 20.10+ and Docker Compose 2.0+
- Python 3.11+
- Kubernetes 1.24+ (for K8s deployment)

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd Scraping_project
```

2. Install dependencies:
```bash
pip install -r requirements.txt
pip install -r dev-requirements.txt  # For development
```

3. Start infrastructure with Docker Compose:
```bash
docker-compose up -d
```

This starts:
- Redis (caching and queue management)
- Stage 1-4 workers
- Prometheus (metrics)
- Grafana (dashboards)

### Running the Pipeline

#### Option 1: Docker Compose (Recommended)
```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f stage2-worker

# Scale workers
docker-compose up -d --scale stage2-worker=3
```

#### Option 2: Kubernetes Deployment

The supported path is the Helm chart; see [k8s/README.md](k8s/README.md):
`helm install scraping-pipeline k8s/helm/scraping-pipeline -n scraping-pipeline`.
`k8s/deployment.yaml` is a minimal quick-start (stage1/stage2 workers and Redis only) with the same health probes:
```bash
# Minimal quick-start (not the full stack)
kubectl apply -f k8s/deployment.yaml

# Check status
kubectl get pods -n uconn-scraper

# Scale workers
kubectl scale deployment stage2-worker --replicas=5 -n uconn-scraper
```

#### Option 3: Individual Components
```bash
# Run Stage 1 worker
python -m src.workers.stage1_worker

# Run Stage 2 worker
python -m src.workers.stage2_worker

# Run with custom config
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
Do not rely on `config/{ENV}.yml` (not present for normal operation).

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

### Environment Variables

```bash
# Redis
REDIS_HOST=redis-service
REDIS_PORT=6379

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

### Redis memory policy: durable keys vs TTL keys (#161)

Redis holds durable crawl state, and losing it is not a cache miss:

| Keys | TTL | If lost |
|---|---|---|
| `seen:urls` and other `seen:*` sets (dedup/claims) | none | every URL looks new, so the crawl starts over |
| Stage queues and the priority queue | none | queued work disappears |
| `depth_spider:last_crawl:*` and other cache-like keys | yes (`ex=`) | recomputed |

Compose, `k8s/deployment.yaml` and Helm (`redis.config.maxmemoryPolicy`) run `--maxmemory-policy volatile-lru`, so only keys with a TTL can be evicted. When Redis reaches `maxmemory` with nothing evictable left, it rejects writes (`OOM command not allowed`) instead of silently dropping a seen set. The seen store then fails closed (see `RedisSeenStoreFailingClosed`).

- **Never use an `allkeys-*` policy.** `RedisHelper` logs an ERROR at connect time if the server runs one.
- **New cache-like keys must set a TTL.** Durable keys must not.
- **Memory SLO.** Stay under 80% of `maxmemory`. The `RedisHighMemory` alert fires above that for 5 minutes. Raise `maxmemory` or drain the queues before writes start failing.

### TLS certificate verification (#584)

Every outbound HTTPS request verifies the server certificate. aiohttp, httpx and requests verify by default. The Scrapy downloader uses `BrowserLikeContextFactory` (set in `src/settings.py` from `src/core/tls_policy.py`) instead of Scrapy's default factory, which accepts any certificate. `tests/unit/test_tls_policy.py` fails CI if code adds `verify=False`, `ssl=False`, `CERT_NONE` or similar bypasses. It also proves end to end that a self-signed server is rejected.

**Exception process.** For a site with a broken chain, fix trust (install the issuing CA on the host or image) rather than disabling checks. As a temporary last resort, set `SCRAPY_TLS_INSECURE=1` for that run. It is logged at ERROR on startup and exported as `scrapy_tls_verification_disabled 1`, so it shows up in monitoring.

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

### Test Results

Current test status:
- **25 tests passing** ✓
- Cache layer: 6/6 tests passing
- Model validation: 10/10 tests passing
- Retry/circuit breaker: 9/9 tests passing

## Monitoring

### Prometheus Metrics

Available at `http://localhost:9090`:

- `pipeline_errors_total`: Total pipeline errors (metrics_exporter.py `errors.total`, named by `statsd_mapping.yml`)
- `redis_queue_length{queue}`: Pending items per queue (metrics_exporter.py `redis.queue.length`)
- `cache_hits_total`: Cache hit count
- `cache_misses_total`: Cache miss count
- `retry_attempts_total`: Retry attempts
- `circuit_breaker_state`: Circuit breaker state (0=closed, 1=open, 2=half-open)

### Grafana Dashboards

Access at `http://localhost:3000` (admin/admin):

- **Pipeline Overview**: End-to-end metrics
- **Worker Performance**: Per-worker statistics
- **Cache Performance**: Hit rates and latency
- **Error Rates**: Error tracking and alerting

### Health Checks

```bash
# Check worker health
curl http://localhost:8000/health

# Check Redis
redis-cli ping

# Check Prometheus
curl http://localhost:9090/-/healthy
```

## Deployment

### Local Development

```bash
docker-compose up -d
```

### Observability (Loki, Jaeger, OpenTelemetry)

`docker-compose.observability.yml` is an overlay on the main file (same network, mounts
under `./monitoring/`). There is no separate standalone production compose file:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d
```

See [../MONITORING.md](../MONITORING.md) for enabling OTEL traces.

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

### Schema Evolution Policy

Every append and overwrite reads the table's current schema from its `_delta_log`, not from process memory. So any number of workers or pods writing the same table agree on one schema.

- **Additive only.** Columns a batch carries that the table lacks are appended (`schema_mode="merge"`) and counted in `delta_schema_evolutions_total{table}`. A column that is null in every row of a batch is skipped until a real value arrives, because it carries no type.
- **Existing column types win.** Rows are cast to the table's types. Values that don't cast are quarantined to `cast_quarantine` (`delta_lake.cast_mode: strict`) or nulled (`coerce`), and counted in `delta_cast_failures_total`.
- **Required columns are never null-filled.** A row missing a non-nullable column is quarantined, in both modes. The rest of the batch is still written.
- **Overwrite replaces rows, not the schema (#509).** `mode="overwrite"` goes through the same cast and additive path and commits with `schema_mode="merge"`. Columns that other writers evolved survive, null in the new rows.
- **Breaking changes** (renames, type narrowing, dropping columns) are never implicit. Rewrite the table deliberately with `write(..., mode="overwrite", schema_overwrite=True)`. That write is always synchronous, logged as `[SCHEMA OVERWRITE]`, and counted in `delta_schema_overwrites_total{table}`.

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

## Project Structure

```
Scraping_project/
├── src/
│   ├── core/                    # Core infrastructure
│   │   ├── models.py            # Pydantic models (Phase 6)
│   │   ├── schemas.py           # PyArrow schemas (Phase 6)
│   │   └── exceptions.py        # Exception hierarchy (Phase 7)
│   ├── utils/                   # Utilities
│   │   ├── cache.py             # Caching (Phase 8)
│   │   ├── retry.py             # Retry/circuit breaker (Phase 7)
│   │   ├── dead_letter_queue.py # DLQ (Phase 7)
│   │   ├── connection_pool.py   # Connection pooling (Phase 8)
│   │   ├── profiler.py          # Profiling (Phase 8)
│   │   └── delta.py             # Delta Lake helpers
│   ├── workers/                 # Worker processes
│   │   ├── stage1_worker.py
│   │   ├── stage2_worker.py
│   │   ├── stage3_worker.py
│   │   └── stage4_worker.py
│   └── stage1/                  # Spiders
│       ├── scout_spider.py
│       ├── depth_spider.py
│       └── js_spider.py
├── tests/                       # Test suite (Phase 9)
│   ├── conftest.py              # Pytest configuration
│   ├── test_cache.py
│   ├── test_models.py
│   ├── test_retry.py
│   └── README.md
├── k8s/                         # Kubernetes (Phase 10)
│   └── deployment.yaml
├── monitoring/                  # Monitoring (Phase 10)
│   ├── prometheus.yml
│   ├── alerting_rules.yml       # alerts (Helm ships an identical copy)
│   └── recording_rules.yml
├── .github/workflows/           # CI/CD (Phase 10)
│   └── ci-cd.yml
├── docker-compose.yml           # Docker Compose (Phase 10)
├── Dockerfile                   # Docker build (Phase 10)
├── mypy.ini                     # Type checking config (Phase 6)
├── pytest.ini                   # Test configuration (Phase 9)
├── requirements.txt             # Python dependencies
└── DEPLOYMENT.md               # Deployment guide (Phase 10)
```

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
