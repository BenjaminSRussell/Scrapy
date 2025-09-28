# Stage 1 Discovery Master Plan

## Objectives

1. Maximise coverage of the `uconn.edu` web estate, including hidden, dynamically generated, and API-served URLs.
2. Preserve compliance (robots, crawl-delay, opt-out) while scaling to millions of URLs.
3. Provide resilient, restartable operations with rich observability and modular heuristics for new domains.

## Phased Roadmap

### Phase 0 – Stabilisation
### Phase 0.1 – Immediate fixes
- Generate and attach `url_hash` for every discovery (canonical SHA-1 over normalised URL).
- Persist `discovery_source` (`seed_csv`, `sitemap`, `data_attr`, `inline_json`, etc.) and `discovery_confidence` with each JSONL row.
- Align counters/tests: rename `unique_hashes_found` expectations or provide alias in spider.
- Replace ad-hoc JSONL rewind with persistent hash index (SQLite/Bloom filter) and checkpoint manifest per batch.
- Ensure Stage 1 outputs match config (`enriched_data.jsonl` path parity).
- Document optional dependency policy (transformers/torch) and move non-essential stacks into extras to keep default installs lean.

### Phase 3b – Validation handoff alignment
- Confirm Stage 2 writes `url_hash`, `discovery_source`, and provenance to JSONL for Stage 3 consumption.
- Add smoke tests across Stage 1→3 verifying schema compatibility and queue execution without subprocess hacks.

### Phase 4b – Observability upgrades
- Emit structured log events for `URL_DISCOVERED`, `DYNAMIC_ENDPOINT_FOUND`, and `FACULTY_PROFILE_DISCOVERED`.
- Track heuristic-specific counters and surface dashboards for success/error rates.
 (blocking defects)
- Patch `common.schemas.ValidationResult` to include `url_hash` so Stage 2 output aligns with code expectations.
- Fix `PipelineOrchestrator.run_concurrent_stage3_enrichment` (`urls_for_enrichment`) to restore CLI Stage 3 runs.
- Add `run_tests.py --smoke` mode that exercises `--stage 1|2|all` without external dependencies.

### Phase 1 – Persistent dedupe & checkpoints
- Replace in-memory `seen_urls` with `common.storage.URLCache` (SQLite) or Bloom-filter-backed buckets per prefix.
- Store per-stage checkpoints (`stage01.checkpoint.json`) tracking last written line and batch IDs.
- Implement idempotent reading: skip JSONL records already marked in the cache to make restarts O(1) not O(n).

### Phase 2 – Seed expansion & feedback loops
- Integrate sitemap/robots bootstrap (`TODO[stage1-hidden-seeds]`).
- Import curated seed lists: registrar directories, academics subdomains, campus services, labs, athletics.
- Periodically diff Stage 2/3 outputs to identify redirect targets and newly minted URLs; feed back into Stage 1 queue.

### Phase 3 – Dynamic runtime heuristics (current work)
- Maintain feature flags (env/config) for each heuristic block: `DATA_ATTR`, `INLINE_JSON`, `INLINE_SCRIPT`, `FORM_ACTION`.
- Add paging/cursor support for API endpoints (`TODO[stage1-ajax-interactions]`)—track visited parameter combinations with TTL caches.
- Parse JavaScript bundle files (`*.js` assets) for endpoint patterns (regex on `/api/`, `.json`, `fetch(`).
- Add rate-limiting for noisy heuristics (`TODO[stage1-dynamic-tuning]`) using per-source counters.

### Phase 4 – Browser-backed discovery
- Deploy Playwright/Selenium microservice for pages flagged as JavaScript dependent.
- Instrument via CDP or mitmproxy to log all network requests; feed new URLs to Stage 1 canonicalisation.
- Target: infinite scroll, “Load more” buttons, SPA routers, lazy feature pages.

### Phase 5 – External intelligence & active probing
- Run site search queries (internal search endpoints, external engines with `site:uconn.edu`).
- Ingest DNS zone listings, campus-hosted XML/JSON feeds (events, news, announcements).
- Export RateMyProfessor cross-links to feed Stage 1 seeds (via faculty profile mapping; see README section).
- Capture archived/legacy URLs from Wayback/Common Crawl; attempt to resolve and update status.

### Phase 6 – Quality assurance & monitoring
- Dashboard metrics: discoveries per heuristic, per depth, per domain; duplicate rates; dynamic/API counts.
- Anomaly detection on heuristic output (e.g., sudden spike in 404s from a new API root).
- Weekly audits comparing coverage with campus sitemaps and search indices.

## Heuristic Catalogue

| Heuristic | Description | Risks / Mitigation |
|-----------|-------------|--------------------|
| Sitemap parser | Parse `sitemap.xml`, nested sitemaps, and alternative formats (RSS, Atom). | Rate-limit; handle large sitemap indexes with streaming parser. |
| Robots bootstrap | Extract `Allow` entries beyond initial seeds. | Respect `Disallow` and `Crawl-delay`. |
| Static link extraction | Existing Scrapy `LinkExtractor` with canonicalisation. | Already in place; ensure deny list remains updated. |
| `data-*` attributes | Scan for `data-url`, `data-api`, etc., capturing hidden links. | False positives; normalise + domain filter. |
| Inline JSON | Load script JSON blocks, extract values with URL-ish keys. | Avoid parsing huge blobs (size guard); skip third-party domains. |
| Inline scripts | Regex for `fetch`, `.ajax`, etc., extracting quoted URLs. | Many relative references; ensure canonicalisation handles query fragments. |
| Form actions | Record GET/POST endpoints for search forms, build minimal query combos. | Need to avoid flooding endpoints; use heuristics for allowable parameters. |
| Pagination tokens | For API endpoints, enumerate `page`, `offset`, `cursor` until response empty or repeated. | Detect loops with `seen_parameter_hashes`. |
| JavaScript bundle scraping | Fetch `*.js` assets (once per domain), parse for URL patterns. | Large files; cache results; respect license/robots for JS crawling. |
| Browser instrumentation | Execute pages in headless browser, log network requests. | Higher resource costs; restrict to flagged pages; parallelise carefully. |
| External search | Query internal search APIs, Google Custom Search (if allowed). | Potential ToS constraints; throttle and cache results. |
| DNS/subdomain enumeration | Use campus-maintained subdomain lists or DNS zone files. | Keep allowlist to avoid security-sensitive hosts. |
| Archive replay | Compare against Wayback/Common Crawl, seed missing URLs. | Some legacy URLs may 404—Mark status in cache to avoid repeat fetches. |

## Metadata & Observability Enhancements
- Add per-item provenance: `discovery_source` (seed, sitemap, data_attr, ajax, etc.), stored alongside `DiscoveryItem`.
- Track `discovery_confidence` (0–1) to prioritise validation order.
- Emit structured logs (JSON) for major events (`URL_DISCOVERED`, `DYNAMIC_ENDPOINT_FOUND`), enabling downstream log aggregation.
- Sample `response.body` hashes for change detection—if a page already visited but hash different, requeue for enrichment.

## Performance Measures
- Parallelise Stage 1 with multiple workers using shared dedupe storage (SQLite/Redis) and partitioned URL namespaces.
- Implement adaptive crawl delay per host based on latency/error rates.
- Use incremental checkpoints to resume without re-reading entire JSONL.

## Compliance & Safety
- Centralise robots parsing; maintain per-domain `CrawlController` with politeness (delay, concurrency).
- Respect login-protected areas; integrate manual approval flow for pages requiring credentials.
- Maintain suppression lists for URLs flagged by compliance review (e.g., HR portals).

## Portability Tips
- Parameterise domain, allowed patterns, and heuristics in config; Stage 1 should be domain-agnostic after this refactor.
- Store heuristics in modular classes/functions with toggles; integrate into orchestrator config for per-project enablement.

---

This plan aligns with the broader roadmap (`docs/pipeline_improvement_plan.md`) and should be updated as milestones are achieved or new signals emerge.
