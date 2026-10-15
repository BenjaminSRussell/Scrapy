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
- Stage 2 `IntelligentAnalyzer` stores a JSON-ready `citations` array on publication pages: DOI regex, Highwire/Dublin Core citation meta, and `scholarly.parse_citations` when that optional callable is installed. No parser package is required. Empty or unreadable input stores `[]` (#651).
- Stage 1 to stage 2 handoff (`StageQueueHandoff`) keeps the in-memory queue at the Redis high watermark (default 10000, same gate as `QueueBacklog`). Producers block until stage 2 drains to the low watermark instead of buffering an unbounded backlog, and each key is delivered once after the consumer catches up (#703).
- Control Center pauses chart history when a metrics scrape is over 2000 series or 50ms to parse, keeps the numeric KPI counters, and shows a banner. Charts return after 3 calm intervals. Override with `window.CC_METRICS_FLOOD` or `?floodMaxSeries=`, `?floodMaxParseMs=`, and `?floodRecover=` (#690).
- Control Center Activity tab has level filter chips (Success, Warning, Danger, Timeout, Info). Each chip shows a count and toggles that level in the browser. No chip pressed shows the full log; Danger alone shows errors only. The overview feed is unchanged (#1027).
- Control Center activity log: a search box filters the 50-message buffer by a case-insensitive substring of the message text, with a Clear search control (and Escape in the field). Level chips still apply on that tab. The overview feed stays unfiltered, and JSON/CSV export still downloads the whole buffer (#1028).
- Dashboard `.skeleton` block: one pulsing gray placeholder for cards (a content bar) and charts (fills the plot). `prefers-reduced-motion: reduce` leaves a static gray and does not pulse (#1025).
- Control Center pipeline stage cards open a side panel with a subset of that stage's metrics and placeholder links for later job and queue detail. Cards are keyboard buttons; Escape closes the panel (#1033).
- Control Center can play a short tone on a new danger activity item. The Sound on danger checkbox is off by default and the choice is remembered in localStorage. Reduced motion and a mute media preference keep it silent (#1037).
- Optional Control Center job captures one Overview smoke screenshot and uploads it as `dashboard-overview-screenshot` (`overview.png` of `#tab-overview`). It is not a pixel-diff gate and does not block the dashboard unit tests (#1044).
- Cross-repo Delta schema registry (`docs/contracts/`) for `pages` (`stage2_page_analysis`), `extractions` (`metadata_queue`) and `jobs` (`stage2_queue`). A pull-request check fails if those writers drop a registered column (#1099).
- Control Center System tab Settings panel lists each Prometheus series the page reads and the UI field it fills (`METRIC_UI_MAP`) (#1038).
- Control Center System Health shows a strip of the last 20 metrics poll outcomes (success or failure) so a flapping exporter is visible, not only the current online/offline state (#1030).
- Control Center comparison toggle overlays a dashed previous-hour baseline on the throughput and performance line charts. Until the metrics feed publishes the `*_prev_hour_avg` gauges, that line is a labeled placeholder, not a computed average (#1035).
- Control Center overview lists the top scrape-target hosts from `scrape_targets`, `pipeline_scrape_targets`, `seed_hosts`, `urls_by_host`, or `urls_by_domain` (label `host` or `domain`), or from an API stub of the same shape. When that metric is missing the card says so instead of inventing domains (#1052).
- Observability pack in `docker-compose.observability.yml`: Prometheus (`127.0.0.1:9095`), Grafana (`127.0.0.1:3001`), and StatsD, started by a bare `up` of that file. The dashboard `ops-lag-depth-commits` shows Kafka lag, Redis frontier depth, crawl rate, and Delta commits, and scrapes optional Rust sitemap `/metrics` plus lakehouse compaction series. `FrontierGrowth` warns when a queue above 1000 gains more than 5000 items in 15 minutes. `QueueBacklog` stays `> 10000`, `KafkaConsumerLagSLO` stays `> 250000`, and `Stage2SuccessSLO` is unchanged (#1097).
- Kafka to Delta ingest poison policy: after `--max-deserialize-failures` (default 3) deserialize failures of one payload, the bytes are produced to the DLQ topic and the offset is committed, including when no Delta rows are buffered, so one malformed message cannot block the partition. `KafkaPoisonMessageRate` warns when the dead-letter rate exceeds 0.1/s for 5 minutes. This is not an SLO (#177).
- Stage 2 can reuse an unchanged HTML body from `src/utils/cache.py` when `STAGE2_CONTENT_CACHE_ENABLED` is set (default off). The entry is keyed by url_hash plus ETag or Last-Modified; a hit skips the network fetch and increments `cache_hit_total`. Responses with neither header are not cached (#478).
- SeedManager reads Redis depth (`ZCARD` of `js_spider:priority_queue`) before enqueueing and skips the write when depth is above the shipped high watermark (10000, `stage1.seed_enqueue_depth_threshold` / `REDIS_QUEUE_HIGH_WATERMARK`) or the depth cannot be read. `seed_enqueue_throttled_total` counts the skipped URLs. The low watermark stays 8000 and `REDIS_QUEUE_BACKPRESSURE_MODE` stays `block` (#1105).
- `make backup` and `make restore` dry-run a Delta lake archive and an optional SQL file (`scripts/automated_backup.sh`, `scripts/restore_backup.sh`). They do not run pg_dump or start services. A real run requires `LAKE_SRC` and `BACKUP_DIR` (and `RESTORE_DEST`) under `/tmp` or the worktree and refuses `data/delta_lake`. `RETENTION_DAYS` (default 7) and `RETENTION_COUNT` (default 0, no cap) prune old archives only inside the chosen backup directory (#300).
- Queue depth, throughput, and error metrics carry a low-cardinality `site_id` label. The value is the configured site id (`SITE_ID`, else `TENANT_ID`, else `REDIS_SITE_ID`, else `default`), never a URL, job id, or raw host. No numeric cap was measured. `QueueBacklog` still fires above 10000 and now names the site. `Stage2SuccessSLO` stays 0.99 over 24h and `KafkaConsumerLagSLO` stays 250000 messages (#192).
- Pipeline SLOs: Stage 2 success >= 99% over 24h (`Stage2SuccessSLO`), Redis queue depth via the existing `QueueBacklog` line (`redis_queue_length` > 10000), and Kafka consume lag via the existing `KafkaConsumerLagSLO` line (250000 messages). Each has a Prometheus query and a panel on the `pipeline-slos` Grafana dashboard (#179).
- `cli.py lake-vacuum` lists the files and bytes a Delta vacuum would delete and removes nothing unless `--apply` is passed (#602).
- Per-table vacuum retention tiers (`delta_lake.table_retention_hours`: default 168h, `stage1_errors` and `stage2_errors` 720h) and file-DLQ cleanup after `dlq_retention_days` (0 keeps everything). Vacuum scheduling stays #266 (#632).
- Offsite candidate review (`cli.py offsite list|accept|reject|promote|gc`) records a decision, promotes accepted URLs to seeds once, and drops stale pending and rejected sightings (#878).
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
- Control Center tabs, health tiles, and the title mark use inline SVG icons (`aria-hidden`) instead of emoji glyphs (#1049).
- Helm Stage 1–4 sizing: dev stays `values.yaml`, `values-small.yaml` is one replica with HPA off, and `values-prod.yaml` raises worker memory limits so a Kafka lag spike does not OOM the pod. `helm template` fails when an enabled workload omits cpu and memory requests or limits. The delta-ingestor `wait-for-kafka` init container sets both. The sizes are in DEPLOYMENT.md (#1104).
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
- Control Center keeps the last 50 activity-log entries in `sessionStorage` for this browser tab. A reload restores them and shows a note that they are from before the reload and may be stale. Clear drops the buffer (#1029).
- Control Center keeps every client time-series (chart history and the activity log) to the last 50 samples with one ring-buffer cap, independent of the chart axis, and cites that cap in the panel help text (#691).
- Stage 2 replicas jitter the 30s poll (`STAGE2_POLL_JITTER_SECONDS`, default ±15s) and take an exclusive Redis lease (`SET stage2:claim:{url_hash} NX EX`) on each pending URL so two workers do not analyze the same batch. If the lease cannot be acquired, including when Redis is down, the URL is not analyzed (#235).
- Crawl counters and the StatsD families shared by the metrics exporter and in-process callers now use one label set (`stage`, `worker_id`, `domain`, and the existing `site_id` where it was already present). A value that fails the site-id pattern is `rejected`. Series are not dropped, and there is no numeric site cap (#243).
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
