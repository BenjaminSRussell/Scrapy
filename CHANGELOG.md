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
- Dead-letter queue for undeliverable Kafka messages, plus an ops CLI (list/stats/show/replay/resolve).
- Bounded (rows/bytes/age) Delta batches with a timer flush for queue and offsite tables.
- Stage 2: configurable per-host concurrency cap; in-request HTTP retries with backoff, jitter and a per-host circuit breaker.
- Sitemap walk caps (depth, URLs, fetches, bytes) and a gzip-bomb guard.
- Grafana scraping-pipeline-health dashboard provisioned in compose and Helm.
- Release images (crawler, metrics, kafka-delta-ingest) smoke-tested on PRs that touch the image recipe (#449).
- Repository policies: SECURITY.md, CODE_OF_CONDUCT.md, this changelog, ADRs, devcontainer (#669 #746 #747 #715 #694).

### Changed
- Bronze contract requires only `url`, `scraped_at_utc` and `spider_name`, shared with the Rust ingestor (#531).
- Kafka messages are keyed by `url_hash`, with an enforceable idempotent producer (#285).
- Undomainable rows are quarantined instead of landing in a hot `domain=unknown` partition.
- ASR no longer sends audio to Google by default (`ASR_PROVIDER` opt-in; local whisper).

### Fixed
- Dashboard UI, accessibility and XSS fixes (#954 #957 #975 #976 #977 #978 #945 #962 #982 #983 #906 #910).
- ASR never leaks temp media files and caps download size (#468).
- Config reloads that fail to parse keep the previous snapshot live.

### Security
- Redis uses `volatile-lru` so seen-sets and queues are never LRU-evicted, and logs an error on `allkeys-*` servers.

<!-- No version has been released yet. The first tag (v0.1.0) turns the block above into
## [0.1.0] - YYYY-MM-DD and adds a link at the bottom of this file. -->
[Unreleased]: https://github.com/BenjaminSRussell/Scrapy/commits/main
