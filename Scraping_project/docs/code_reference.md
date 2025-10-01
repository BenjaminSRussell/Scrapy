# Codebase Reference & Issue Guide

This document gives a quick map of the UConn scraping pipeline, explains why key values exist, and provides current status after recent improvements.

## **✅ Recent Fixes Completed (Sept 2025)**

- **Stage 3 Pipeline**: Created missing `scrapy.cfg` and `src/settings.py` - Stage 3 enrichment now works
- **Import Consistency**: Standardized all imports to use `src.` prefix across the entire codebase
- **Test Reliability**: All 120+ tests passing with improved coverage and stability
- **Python 3.12 Compatibility**: Modern type hints and syntax throughout
- **Scrapy Configuration**: Proper spider modules and pipeline configuration

## Pipeline Status Overview

| Stage | Status | Last Working | Key Files |
|-------|--------|--------------|-----------|
| **Stage 1 (Discovery)** | ✅ **Working** | Sept 2025 | `src/stage1/discovery_spider.py`, `src/stage1/discovery_pipeline.py` |
| **Stage 2 (Validation)** | ✅ **Working** | Sept 2025 | `src/stage2/validator.py` |
| **Stage 3 (Enrichment)** | ✅ **Working** | Sept 2025 | `src/stage3/enrichment_spider.py`, `src/stage3/enrichment_pipeline.py` |
| **Orchestrator** | ✅ **Working** | Sept 2025 | `src/orchestrator/main.py`, `src/orchestrator/pipeline.py` |

## Legend: Current Status & Counters

| Component | Location | Purpose | Status |
|-----------|----------|---------|--------|
| Dynamic Discovery Throttling | `src/stage1/discovery_spider.py` | Intelligent throttling prevents noisy URL generation from heuristics | ✅ Enhanced |
| Checkpoint Support | `src/stage2/validator.py` | Resume capability for interrupted validation batches | ✅ Implemented |
| Schema Versioning | `src/common/schemas.py` | Version 2.0 schemas with model-ready enhancements | ✅ Complete |
| Data Path Consolidation | Configuration & Orchestrator | All artifacts organized under `data/` directory | ✅ Complete |
| Pipeline Queues | `src/orchestrator/pipeline.py` | Async batch processing with deadlock prevention | ✅ Working |
| URL Hash Linkage | All pipeline stages | Critical field linking stages together | ✅ Complete |

## Stage 1 – Discovery (`src/stage1`) ✅

Core files: `discovery_spider.py`, `discovery_pipeline.py`.

- **Seeds**: read from `data/raw/uconn_urls.csv`. `_clean_seed_url` sanitises common export artefacts (extra schemes, backslashes). `seed_count`, `sanitized_seed_count`, `malformed_seed_skipped` are logged at startup.
- **Canonicalisation**: `canonicalize_url_simple` (wrapping `common.urls.normalize_url`) is the single place URLs are normalised before dedupe.
- **✅ Sitemap/Robots Bootstrap**: `_generate_sitemap_requests()` automatically discovers URLs from sitemaps and robots.txt
- **Dynamic discovery**: After link extraction, `_discover_dynamic_sources` scans:
  - `data-*` attributes (`DATA_ATTRIBUTE_CANDIDATES`).
  - Inline JSON and `application/json` scripts (recursively via `_extract_urls_from_json`).
  - Inline scripts containing AJAX hints (`DYNAMIC_SCRIPT_HINTS`).
  - Form `action` attributes (common for hidden search APIs).
  - **✅ Pagination URLs**: `_generate_pagination_urls()` creates common pagination patterns for API endpoints
- **Output**: `Stage1Pipeline` writes JSONL to `data/processed/stage01/new_urls.jsonl`.
- **Counters reported on close**: total pages parsed, unique URLs, duplicates skipped, dynamic/API discoveries, depth distribution, and top referring sources.

## Stage 2 – Validation (`src/stage2`) ✅

Core file: `validator.py`.

- **Config**: `max_workers`, `timeout`, and `output_file` come from `config/<env>.yml` under `stages.validation`.
- **Concurrency**: `BatchQueue` feeds batches sized to `max_workers`. `validate_batch` spins an `aiohttp.ClientSession` with pooled connections.
- **Execution**: `_validate_with_session` prefers `HEAD`; falls back to `GET` if `HEAD` is inconclusive or unsupported. Retries/backoff logic ensures 3 attempts before emitting timeout/client-error results.
- **✅ Output**: `data/processed/stage02/validated_urls.jsonl` with complete `ValidationResult` including `url_hash` field.
- **Optional SSL relaxation**: connector disables hostname verification to prevent dev certificates from blocking.

## Stage 3 – Enrichment (`src/stage3`) ✅

Core files: `enrichment_spider.py`, `enrichment_pipeline.py`.

- **✅ Configuration**: Now properly configured with `scrapy.cfg` and `src/settings.py`
- **Inputs**: Takes either an orchestrator-supplied list or reads `data/processed/stage02/validated_urls.jsonl` (only `is_valid` URLs).
- **Extraction**: uses XPath to gather title/body text, `src.common.nlp.extract_entities_and_keywords` for NLP, path-based tags, and checks for PDF/audio links. Optional HuggingFace scoring requires extra dependencies.
- **Output**: JSONL at `data/processed/stage03/enriched_content.jsonl` with timestamped enrichment metadata.
- **✅ Direct Execution**: Works via `scrapy crawl enrichment -a urls_file=<file>`

## Orchestrator (`src/orchestrator`) ✅

- **`main.py`**: CLI parser (`--env`, `--stage`, `--config-only`, `--log-level`). Loads config, sets up logging, initialises `PipelineOrchestrator`.
- **`pipeline.py`**: Contains `BatchQueue` and queue population/consumption logic.
  - **✅ Enhanced Implementation**: All imports use `src.` prefix, checkpoint support added
  - `batch_size` defaults to Stage 2 `max_workers` and Stage 3 `batch_size` from config.
  - `populate_stage2_queue` / `populate_stage3_queue` read JSONL files and push `BatchQueueItem`s.
  - `run_concurrent_stage2_validation` runs producer/consumer concurrently so large inputs don't deadlock the queue.
  - ✅ Stage 3 concurrency uses organized temp files in `data/temp/` directory with proper cleanup.
- **`config.py`**: Loads YAML and applies env overrides. Keys you'll see in the code include `SCRAPY_CONCURRENT_REQUESTS`, `STAGE1_MAX_DEPTH`, etc.

## Common Modules (`src/common`) ✅

- **`logging.py`**: `setup_logging` attaches console + optional rotating file handler. CLI scripts call this at startup.
- **`storage.py`**: Contains `JSONLStorage` and `URLCache` (SQLite). Future persistence work will lean on the SQLite-backed cache to avoid rescanning JSONL files.
- **`nlp.py`**: `NLPRegistry` loads spaCy and optional transformer pipeline. Defaults: `MAX_TEXT_LENGTH=20000`, `TOP_KEYWORDS=15`.
- **`urls.py`**: Provides `canonicalize_url_simple` and `is_valid_uconn_url`. Normalisation removes default ports, resolves dot segments, and filters invalid schemes.
- **`schemas.py`**: ✅ All dataclasses now include required fields like `url_hash`

## Configuration Files ✅

- **✅ `scrapy.cfg`**: Created - enables proper Scrapy project configuration
- **✅ `src/settings.py`**: Created - contains Scrapy settings with correct spider modules and pipelines
- **`config/development.yml`** / **`config/production.yml`**: Stage-specific settings and parameters
- **`pytest.ini`**: Test configuration with custom markers for different test types

## Data & Configuration Paths

- `data/raw/uconn_urls.csv` – seeds.
- `data/processed/stage01/new_urls.jsonl` – discovery output.
- `data/processed/stage02/validation_output.jsonl` – validator output.
- `data/processed/stage03/enriched_data.jsonl` – enrichment output.
- `data/logs/` – rotating logs if enabled.
- `.scrapy/` – Scrapy's internal cache and state directory (auto-created)
- Optional directories (`data/catalog`, `data/cache`, `data/exports`) are currently empty placeholders.

## Current Status

All major pipeline issues have been resolved. The UConn web scraping pipeline is now fully operational with:

- Stage 1 Discovery: ✅ Working with enhanced dynamic throttling
- Stage 2 Validation: ✅ Working with checkpoint support
- Stage 3 Enrichment: ✅ Working with model-ready schema enhancements
- Data Path Consolidation: ✅ All artifacts consolidated under `data/`
- Durable Processing: ✅ Checkpoint and resume capability implemented
- Schema Evolution: ✅ Version 2.0 schemas with provenance tracking

## Testing Overview ✅

- **Run all tests**: `python -m pytest` (120+ tests, all passing)
- **Quick subset**: `python -m pytest tests/common/ -v`
- **Integration tests**: `python -m pytest -m integration`
- **Configuration**: Strict enforcement via `pytest.ini` with custom markers
- **Coverage**: Comprehensive coverage across common modules, schemas, storage, NLP, and integration

## Execution Examples

### Orchestrator Mode (Recommended)
```bash
# Run Stage 1 (Discovery)
python main.py --env development --stage 1

# Run Stage 2 (Validation)
python main.py --env development --stage 2

# Run Stage 3 (Enrichment)
python main.py --env development --stage 3

# Run full pipeline (stages 1-3)
python main.py --env development --stage all
```

### Individual Scrapy Spiders (for debugging)
```bash
# Stage 1 Discovery
scrapy crawl discovery

# Stage 3 Enrichment
scrapy crawl enrichment -a urls_file=data/processed/stage02/validation_output.jsonl
```

## Quick Reference: Config Values

| Config Key | Default (development) | Used In | Status |
|------------|----------------------|---------|--------|
| `stages.discovery.max_depth` | `3` | Discovery spider request depth | ✅ Working |
| `stages.validation.max_workers` | `16` | Batch queue size for validator | ✅ Working |
| `stages.validation.timeout` | `15s` | `aiohttp` client timeout | ✅ Working |
| `stages.enrichment.output_file` | `data/processed/stage03/enriched_content.jsonl` | Enrichment pipeline output | ✅ Working |
| `scrapy.download_delay` | `0.1s` | Stage 1 spider throttling | ✅ Working |
| `logging.level` | `INFO` | Default log level | ✅ Working |

## Where to Look Next

- `README.md` – high-level project overview, setup, and usage instructions.
- Individual stage directories (`src/stage1/`, `src/stage2/`, `src/stage3/`) for implementation details.