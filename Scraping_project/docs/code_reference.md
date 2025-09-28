# Codebase Reference & Issue Guide

This document gives a quick map of the UConn scraping pipeline, explains why key values exist, and calls out the highest priority issues so new contributors can navigate the repo without diff diving.

## Legend: Priority Tags & Counters

| Tag / Field | Where | Purpose |
|-------------|-------|---------|
| `TODO[stage1-hidden-seeds]` | `src/stage1/discovery_spider.py` | Add sitemap/robots bootstrap to discover new entry points without editing the CSV. High priority for URL coverage. |
| `TODO[stage1-dynamic-tuning]` | `src/stage1/discovery_spider.py` | Throttle dynamic heuristics when they produce noisy URLs. Enables runtime switches once monitoring is in place. |
| `TODO[stage1-ajax-interactions]` | `src/stage1/discovery_spider.py` | Follow-up work to cycle through paging tokens (e.g., `?page=`) once base endpoints are known. Medium priority. |
| `dynamic_urls_found` / `api_endpoints_found` | discovery spider counters | Incremented whenever AJAX endpoints or API URLs are discovered. Watch these to validate heuristics. |
| `depth_yields` | discovery spider | Histogram of discoveries per crawl depth; used in crawl summary. |
| `stage1_to_stage2_queue` / `stage2_to_stage3_queue` | `src/orchestrator/pipeline.py` | Async batch queues sized from config; prevent deadlocks on large crawls. |
| `ValidationResult` (missing `url_hash`) | `src/common/schemas.py` | Current bug: validator returns `url_hash` but dataclass lacks the field. Fix lives in stabilization workstream. |
| `urls_for_enrichment` NameError | `src/orchestrator/pipeline.py` | Stage 3 orchestration bug – CLI `--stage 3` fails until addressed. |

## Stage 1 – Discovery (`src/stage1`)

Core files: `discovery_spider.py`, `discovery_pipeline.py`.

- **Seeds**: read from `data/raw/uconn_urls.csv`. `_clean_seed_url` sanitises common export artefacts (extra schemes, backslashes). `seed_count`, `sanitized_seed_count`, `malformed_seed_skipped` are logged at startup.
- **Canonicalisation**: `canonicalize_url_simple` (wrapping `common.urls.normalize_url`) is the single place URLs are normalised before dedupe.
- **Dynamic discovery**: After link extraction, `_discover_dynamic_sources` scans:
  - `data-*` attributes (`DATA_ATTRIBUTE_CANDIDATES`).
  - Inline JSON and `application/json` scripts (recursively via `_extract_urls_from_json`).
  - Inline scripts containing AJAX hints (`DYNAMIC_SCRIPT_HINTS`).
  - Form `action` attributes (common for hidden search APIs).
  All candidates run through `_normalize_candidate` to stay on `*.uconn.edu`. New URLs go through `_process_candidate_url`, which adds them to `seen_urls`, yields a `DiscoveryItem`, and schedules a follow-up request if depth allows.
- **Output**: `Stage1Pipeline` writes JSONL to `data/processed/stage01/new_urls.jsonl`. On spider restart it rewinds the entire file to rebuild `seen_hashes` – a major performance issue flagged in the improvement plan.
- **Counters reported on close**: total pages parsed, unique URLs, duplicates skipped, dynamic/API discoveries, depth distribution, and top referring sources.

## Stage 2 – Validation (`src/stage2`)

Core file: `validator.py` (ignore removed duplicate `validator 2.py`).

- **Config**: `max_workers`, `timeout`, and `output_file` come from `config/<env>.yml` under `stages.validation`.
- **Concurrency**: `BatchQueue` feeds batches sized to `max_workers`. `validate_batch` spins an `aiohttp.ClientSession` with pooled connections.
- **Execution**: `_validate_with_session` prefers `HEAD`; falls back to `GET` if `HEAD` is inconclusive or unsupported. Retries/backoff logic ensures 3 attempts before emitting timeout/client-error results.
- **Output path**: `data/processed/stage02/validated_urls.jsonl` with one `ValidationResult` per line. Bug note: dataclass currently lacks `url_hash`; real runs will include the field once the stabilization fix lands.
- **Optional SSL relaxation**: connector disables hostname verification to prevent dev certificates from blocking.

## Stage 3 – Enrichment (`src/stage3`)

Core files: `enrichment_spider.py`, `enrichment_pipeline.py`.

- **Inputs**: Takes either an orchestrator-supplied list (when Stage 3 queue works) or falls back to reading `data/processed/stage02/validated_urls.jsonl` (only `is_valid` URLs).
- **Extraction**: uses XPath to gather title/body text, `common.nlp.extract_entities_and_keywords` for NLP, path-based tags, and checks for PDF/audio links. Optional HuggingFace scoring requires extra dependencies (see requirements).
- **Output**: JSONL at `data/processed/stage03/enriched_data.jsonl` with timestamped enrichment metadata.
- **Orchestrator gap**: `PipelineOrchestrator.run_concurrent_stage3_enrichment` references `urls_for_enrichment` before assignment; direct Scrapy execution is recommended until fixed.

## Orchestrator (`src/orchestrator`)

- **`main.py`**: CLI parser (`--env`, `--stage`, `--config-only`, `--log-level`). Loads config, sets up logging, initialises `PipelineOrchestrator`.
- **`pipeline.py`**: Contains `BatchQueue` and queue population/consumption logic.
  - `batch_size` defaults to Stage 2 `max_workers` and Stage 3 `batch_size` from config.
  - `populate_stage2_queue` / `populate_stage3_queue` read JSONL files and push `BatchQueueItem`s.
  - `run_concurrent_stage2_validation` runs producer/consumer concurrently so large inputs don’t deadlock the queue.
  - Stage 3 concurrency currently shells out to `scrapy crawl` with a temp file containing URLs; bug triggered if `urls_for_enrichment` never defined.
- **`config.py`**: Loads YAML and applies env overrides. Keys you’ll see in the code include `SCRAPY_CONCURRENT_REQUESTS`, `STAGE1_MAX_DEPTH`, etc.

## Common Modules (`src/common`)

- **`logging.py`**: `setup_logging` attaches console + optional rotating file handler. CLI scripts call this at startup.
- **`storage.py`**: Contains `JSONLStorage` and `URLCache` (SQLite). Future persistence work will lean on the SQLite-backed cache to avoid rescanning JSONL files.
- **`nlp.py`**: `NLPRegistry` loads spaCy and optional transformer pipeline. TODO there reminds us to fail gracefully when models are missing. Defaults: `MAX_TEXT_LENGTH=20000`, `TOP_KEYWORDS=15`.
- **`urls.py`**: Provides `canonicalize_url_simple` and `is_valid_uconn_url`. Normalisation removes default ports, resolves dot segments, and filters invalid schemes.

## Data & Configuration Paths

- `data/raw/uconn_urls.csv` – seeds.
- `data/processed/stage01/new_urls.jsonl` – discovery output.
- `data/processed/stage02/validated_urls.jsonl` – validator output.
- `data/processed/stage03/enriched_data.jsonl` – enrichment output.
- `data/logs/` – rotating logs if enabled.
- Optional directories (`data/catalog`, `data/cache`, `data/exports`) are currently empty placeholders pending future cataloguing/export work.

## Known Issues (Priority Order)

1. **Stabilise pipeline surface area**
   - Add `url_hash` to `ValidationResult`.
   - Fix Stage 3 orchestrator (`urls_for_enrichment`).
   - Add smoke-test profile (`run_tests.py --smoke`).
2. **Durable batching/restarts** – unify around `URLCache`, add checkpoints per stage.
3. **Stage 1 discovery expansion** – implement sitemap bootstrap, dynamic throttling, paging heuristics.
4. **Data path consolidation** – ensure all artefacts live under `Scraping_project/data/` and migrate legacy folders (partial work already done).
5. **Stage-scoped logging ergonomics** – CLI flags for checkpoints/resume, JSON logs.
6. **Model-ready schemas** – add summarisation & provenance fields with schema versioning.

(These correspond to `docs/pipeline_improvement_plan.md`.)

## Testing Overview

- Run all tests: `python -m pytest`.
- Strict configuration enforced via `pytest.ini` (`--strict-markers`, `--strict-config`, `--durations=10`).
- Critical suites are listed in the README; use markers (`-m integration`, `-m slow`) to target subsets during investigations.

## Quick Reference: Config Values Seen in Code

| Config Key | Default (development) | Used In |
|------------|----------------------|---------|
| `stages.discovery.max_depth` | `3` | Discovery spider request depth. |
| `stages.discovery.batch_size` | `1000` | Not currently used; placeholder for future batching. |
| `stages.validation.max_workers` | `16` | Batch queue size for validator; drives `aiohttp` concurrency. |
| `stages.validation.timeout` | `15s` | `aiohttp` client timeout. |
| `stages.enrichment.output_file` | `data/processed/stage03/enriched_data.jsonl` | Enrichment pipeline output path. |
| `scrapy.download_delay` | `0.1s` | Applies to Stage 1 spider when orchestrated. |
| `logging.level` | `INFO` | default log level for `setup_logging`. |

## Where to Look Next

- `README.md` – high-level usage & testing summary.
- `docs/pipeline_improvement_plan.md` – prioritised roadmap with context on future tasks.
- `run_tests.py` – multi-profile test runner that produces JSON summaries (future home for smoke/regression reports).

