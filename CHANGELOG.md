# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and
the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html) as
described in [docs/RELEASING.md](docs/RELEASING.md#versioning-policy).

**How to update:** every PR that changes behaviour adds one line under
`## [Unreleased]` in the matching section (`Added`, `Changed`, `Deprecated`,
`Removed`, `Fixed`, `Security`) and ends it with the issue number, e.g. `(#577)`.
Docs-, test- and CI-only PRs may skip it. At release time the `Unreleased` block is
renamed to the new version and date (see the release checklist).

## [Unreleased]

### Added
- Pipeline SLOs: Stage 2 success >= 99% over 24h (`Stage2SuccessSLO`), Redis queue depth via the existing `QueueBacklog` line (`redis_queue_length` > 10000), and Kafka consume lag via the existing `KafkaConsumerLagSLO` line (250000 messages). Each has a Prometheus query and a panel on the `pipeline-slos` Grafana dashboard (#179).
- Redis queue producers block, or shed low-priority URLs, when depth passes a configurable high watermark and resume at the low watermark. `redis_queue_backpressure_total{queue,action}` counts those events. Defaults match the existing `QueueBacklog` alert (`redis_queue_length` > 10000) (#196).
- Stage 4 summarization folds local audio and text fixtures through `async_asr_processor` when `STAGE4_ASR_ENABLED` is set (default off), and records `asr_jobs_total`, `asr_latency` and `asr_failures_total` (#1101).
- Stage 1 can rotate egress proxies and temporarily quarantine ones that return 407/429 or time out. Off unless `PROXY_LIST` or `stage1.proxy.urls` is set (#198).
- Docker Compose profiles `core`, `monitoring`, `streaming` and `observability` (ops dashboard, optional Kafka, OTEL overlay). Stage 4 stays on `ml` / `full` so the core image does not include torch (#145).
- Ops CLI is listed in CONTRIBUTING, `.env.example` marks required and optional variables, and `access_grafana.sh` opens local Compose Grafana when Kubernetes is not running (#239 #345 #379 #382).
- Config-driven Delta partitions (`domain` or `date`) on newly created tables, a declared `metadata_queue` with a schema and day partition, and warn/reject for writes to undeclared tables (#261 #236 #268 #433).
- `cli.py --profile` writes a cProfile `.json` and `.pstats` for one command (`make profile CMD="..."`); nothing is recorded unless the flag is passed (#465).
- Dead-letter queue for undeliverable Kafka messages, plus an ops CLI (list/stats/show/replay/resolve).
- Bounded (rows/bytes/age) Delta batches with a timer flush for queue and offsite tables.
- Stage 2: configurable per-host concurrency cap; in-request HTTP retries with backoff, jitter and a per-host circuit breaker.
- Sitemap walk caps (depth, URLs, fetches, bytes) and a gzip-bomb guard.
- Grafana scraping-pipeline-health dashboard provisioned in compose and Helm.
- Release images (crawler, metrics, kafka-delta-ingest) smoke-tested on PRs that touch the image recipe (#449).
- Repository policies: SECURITY.md, CODE_OF_CONDUCT.md, this changelog, ADRs, devcontainer (#669 #746 #747 #715 #694).

### Changed
- Production Helm no longer runs the single-replica Redis. `redis.external.host` and `redis.external.port` become `REDIS_HOST` and `REDIS_PORT` (password stays `REDIS_PASSWORD`; do not set `REDIS_URL`). AOF can recover data after the node returns, and it does not keep the queue available during node loss (#216).
- Seen-URL Redis keys are `site:{site_id}:{prefix}:urls` (for example `site:uconn:seen:urls`). Unscoped `seen:urls` and `scout:urls` are not read; rename them before deploy. `SITE_ID`, else `TENANT_ID`, else `REDIS_SITE_ID`, else `default` (#191).
- Scrapy and Stage 2 share one retry policy (`retry` in config.yml, otherwise dev vs production defaults). Production uses more attempts; 429 and 503 stay retryable (#215).
- `start.py` and `shutdown.py` use `docker-compose` when it is installed, otherwise the `docker compose` plugin (`COMPOSE_CMD` overrides). Dry runs do not probe the plugin (#342).
- Redis is one env contract: `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` and `REDIS_DB`, with `REDIS_URL` only a fallback for values those leave unset (#511).
- Bronze contract requires only `url`, `scraped_at_utc` and `spider_name`, shared with the Rust ingestor (#531).
- Kafka messages are keyed by `url_hash`, with an enforceable idempotent producer (#285).
- Undomainable rows are quarantined instead of landing in a hot `domain=unknown` partition.
- ASR no longer sends audio to Google by default (`ASR_PROVIDER` opt-in; local whisper).

### Deprecated
- `python reseed.py`, `python drain_lake.py`, `python audit_file_counts.py`, and `python monitor_and_trigger_depth.py` at the Scraping_project root. Run `python scripts/<name>.py` from `Scraping_project/` instead. `cli.py` stays the root Python entrypoint (#568).

### Fixed
- Stage 4 entity updates load stored facts for that entity id, append and dedupe them, and rewrite the summary only when that entity's cluster changed. A new document does not recompute unrelated entities (#474).
- Circuit breakers share open state and the consecutive-failure count through Redis, keyed by name and host, so one worker's trip is visible to the others on their next check and still open after a restart. The metrics exporter emits `circuit_breaker_state{name,host}` (0 closed, 1 open, 2 half-open). Set `CIRCUIT_BREAKER_SHARED=0` to keep the old process-local breakers (#171).
- CLI and orchestrator startup reject a config.yml whose redis or kafka fields have the wrong type or an unknown key, with the field path in the error, instead of failing later inside workers (#237).
- Stage 3 keeps MinHash fingerprints for the rest of the run and in Redis, so a duplicate document in a later batch or after a restart is skipped and `stage3_documents_deduplicated_total` counts it (#222).
- Item pipelines no longer swallow handled errors with a bare `pass`; they log `stage` and `reason` and increment `pipeline_errors_total` (#219).
- Docker HEALTHCHECK probes PID 1 and configured Redis/TCP dependencies instead of always succeeding, and the docs no longer point at a missing `/health` route (#542).
- Delta writes and merges keep an existing table's partition layout when it differs from `delta_lake.partitions`, instead of failing every commit (#261).
- Dashboard UI, accessibility and XSS fixes (#954 #957 #975 #976 #977 #978 #945 #962 #982 #983 #906 #910).
- ASR never leaks temp media files and caps download size (#468).
- Config reloads that fail to parse keep the previous snapshot live.
- Control Center: keyboard tabs, landmarks, contrast, focus, reduced motion, a wrapping mobile topbar, and loading, stale, and error states (#153 #155 #332 #340 #341 #343 #349 #351 #352 #362).

### Security
- Production Helm overlay turns on default-deny NetworkPolicies for Redis, Kafka, Postgres and the scrapers. The delta ingestor may dial Redis, and Grafana may install the Redis datasource plugin over public HTTPS (same private and link-local exclusions as scrape egress; the plugin is not baked into the image). Alertmanager external receivers stay closed. `networkPolicy.breakGlass` is the debug override and must not stay on (#190).
- Compose reads the Postgres superuser password from `POSTGRES_PASSWORD` (local default `postgres`), and SECURITY.md covers secret handling and rotation (#482).
- `cli.py setup` downloads the revisions pinned in `models.lock.json` and fails closed on a missing file, a size mismatch, or a digest mismatch (#486).
- Redis uses `volatile-lru` so seen-sets and queues are never LRU-evicted, and logs an error on `allkeys-*` servers.

<!-- No version has been released yet. The first tag (v0.1.0) turns the block above into
## [0.1.0] - YYYY-MM-DD and adds a link at the bottom of this file. -->
[Unreleased]: https://github.com/BenjaminSRussell/Scrapy/commits/main
