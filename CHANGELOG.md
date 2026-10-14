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
- Ops CLI is listed in CONTRIBUTING, `.env.example` marks required and optional variables, and `access_grafana.sh` opens local Compose Grafana when Kubernetes is not running (#239 #345 #379 #382).
- Config-driven Delta partitions (`domain` or `date`) on newly created tables, a declared `metadata_queue` with a schema and day partition, and warn/reject for writes to undeclared tables (#261 #236 #268 #433).
- Dead-letter queue for undeliverable Kafka messages, plus an ops CLI (list/stats/show/replay/resolve).
- Bounded (rows/bytes/age) Delta batches with a timer flush for queue and offsite tables.
- Stage 2: configurable per-host concurrency cap; in-request HTTP retries with backoff, jitter and a per-host circuit breaker.
- Sitemap walk caps (depth, URLs, fetches, bytes) and a gzip-bomb guard.
- Grafana scraping-pipeline-health dashboard provisioned in compose and Helm.
- Release images (crawler, metrics, kafka-delta-ingest) smoke-tested on PRs that touch the image recipe (#449).
- Repository policies: SECURITY.md, CODE_OF_CONDUCT.md, this changelog, ADRs, devcontainer (#669 #746 #747 #715 #694).

### Changed
- `start.py` and `shutdown.py` use `docker-compose` when it is installed, otherwise the `docker compose` plugin (`COMPOSE_CMD` overrides). Dry runs do not probe the plugin (#342).
- Redis is one env contract: `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` and `REDIS_DB`, with `REDIS_URL` only a fallback for values those leave unset (#511).
- Bronze contract requires only `url`, `scraped_at_utc` and `spider_name`, shared with the Rust ingestor (#531).
- Kafka messages are keyed by `url_hash`, with an enforceable idempotent producer (#285).
- Undomainable rows are quarantined instead of landing in a hot `domain=unknown` partition.
- ASR no longer sends audio to Google by default (`ASR_PROVIDER` opt-in; local whisper).

### Fixed
- Docker HEALTHCHECK probes PID 1 and configured Redis/TCP dependencies instead of always succeeding, and the docs no longer point at a missing `/health` route (#542).
- Delta writes and merges keep an existing table's partition layout when it differs from `delta_lake.partitions`, instead of failing every commit (#261).
- Dashboard UI, accessibility and XSS fixes (#954 #957 #975 #976 #977 #978 #945 #962 #982 #983 #906 #910).
- ASR never leaks temp media files and caps download size (#468).
- Config reloads that fail to parse keep the previous snapshot live.
- Control Center: keyboard tabs, landmarks, contrast, focus, reduced motion, a wrapping mobile topbar, and loading, stale, and error states (#153 #155 #332 #340 #341 #343 #349 #351 #352 #362).

### Security
- Compose reads the Postgres superuser password from `POSTGRES_PASSWORD` (local default `postgres`), and SECURITY.md covers secret handling and rotation (#482).
- Redis uses `volatile-lru` so seen-sets and queues are never LRU-evicted, and logs an error on `allkeys-*` servers.

<!-- No version has been released yet. The first tag (v0.1.0) turns the block above into
## [0.1.0] - YYYY-MM-DD and adds a link at the bottom of this file. -->
[Unreleased]: https://github.com/BenjaminSRussell/Scrapy/commits/main
