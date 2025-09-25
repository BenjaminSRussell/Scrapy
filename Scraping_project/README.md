# UConn Web Scraping Pipeline

A staged scraping pipeline tailored to the `uconn.edu` domain. Stage 1 discovers pages with Scrapy, Stage 2 validates them with an async HTTP client, and Stage 3 enriches content with NLP helpers. The entry point (`main.py`) wires the stages together through the async orchestrator in `src/orchestrator/main.py`.

## Features
- Scrapy-based discovery seeded from `data/raw/uconn_urls.csv`, with URL canonicalisation and SHA-1 deduplication (`src/stage1`).
- Async validation of discovered URLs via `aiohttp`, including HEAD→GET fallback and JSONL persistence (`src/stage2/validator.py`).
- Content enrichment spider with spaCy-powered entity/keyword extraction and optional HuggingFace scoring (`src/stage3/enrichment_spider.py`).
- Config-driven behaviour (`config/development.yml`, `config/production.yml`) and centralised logging (`src/common/logging.py`).
- Batch queue orchestration designed to avoid deadlocks when processing large URL sets (`src/orchestrator/pipeline.py`).

## Requirements
- Python 3.8+
- Python packages in `requirements.txt`
- spaCy model download (required for NLP features):
  ```bash
  python -m spacy download en_core_web_sm
  ```
- Optional but supported for Stage 3 scoring: `sentence-transformers`, `transformers`, `huggingface-hub`

## Quick Start
1. Create & activate a virtual environment (recommended).
2. Install dependencies and the spaCy model:
   ```bash
   pip install -r requirements.txt
   python -m spacy download en_core_web_sm
   ```
3. Provide seed URLs: create `data/raw/uconn_urls.csv` with one URL per line (headerless CSV).
4. Run stages from the project root:
   ```bash
   # Stage 1 – discovery only
   python main.py --stage=1 --env=development

   # Stage 2 – validation only (expects Stage 1 output)
   python main.py --stage=2 --env=development

   # Stage 3 is currently not runnable through the orchestrator due to a bug (see Known Issues).
   ```

`main.py --config-only --env=<env>` prints the resolved configuration without executing any stage.

## Command Line Interface
```bash
python main.py --help
```
Options implemented in `src/orchestrator/main.py`:
- `--env {development,production}` – selects the YAML config (default `development`).
- `--stage {1,2,3,all}` – chooses which stages to execute (default `all`).
- `--config-only` – print the merged config and exit.
- `--log-level {DEBUG,INFO,WARNING,ERROR}` – overrides console/log file verbosity.

> ⚠️ `--stage=3` and `--stage=all` currently raise a `NameError` (`urls_for_enrichment`) because Stage 3 orchestration is unfinished. Run discovery/validation only until the bug is resolved (see Known Issues for a workaround suggestion).

## Pipeline Stages
### Stage 1 — Discovery (`src/stage1`)
- `DiscoverySpider` reads seed URLs from `data/raw/uconn_urls.csv`, canonicalises them, and performs breadth-first crawling limited to `uconn.edu`.
- Link extraction ignores common binary/static file extensions, and discovered URLs are deduplicated via in-memory SHA-1 hashes (`common/urls.py`).
- Metrics such as depth distribution and duplicate counts are logged when the spider closes.
- `Stage1Pipeline` persists unique discoveries to `data/processed/stage01/new_urls.jsonl`. On start-up it rewinds the JSONL file to rebuild the dedupe set (not ideal for very large histories; see Known Issues).

### Stage 2 — Validation (`src/stage2/validator.py`)
- `URLValidator` consumes Stage 1 output, validating URLs concurrently with `aiohttp`.
- Each URL is checked with HEAD, then GET if necessary, and results are written as `ValidationResult` JSON objects to `data/processed/stage02/validated_urls.jsonl`.
- URLs are marked `is_valid` when they return 2xx/3xx responses with `text/html` content.
- The orchestrator’s `BatchQueue` feeds the validator without blocking, fixing the >10k item deadlock noted in comments.

### Stage 3 — Enrichment (`src/stage3`)
- `EnrichmentSpider` enriches validated URLs: extracts title/body text, entities/keywords via `common/nlp.py`, derives content tags from the URL path, and flags PDF/audio links.
- The spider can optionally score outbound links using sentence-transformer embeddings when the optional dependencies are installed.
- `Stage3Pipeline` writes enrichment results to `data/processed/stage03/enriched_data.jsonl` (path controlled via YAML config).
- spaCy shortages are handled gracefully: no model → empty entity/keyword lists.
- **Orchestrator status:** `PipelineOrchestrator.run_concurrent_stage3_enrichment` references an undefined `urls_for_enrichment` variable, so the CLI stage fails today. Until fixed, run the spider manually with Scrapy and provide the pipeline settings, e.g.:
  ```bash
  python -m scrapy runspider src/stage3/enrichment_spider.py \
    -s ITEM_PIPELINES="{'stage3.enrichment_pipeline.Stage3Pipeline': 300}" \
    -s STAGE3_OUTPUT_FILE=data/processed/stage03/enriched_data.jsonl
  ```
  (This command assumes Stage 2 output exists; adjust settings as needed.)

## Configuration
- `Config` (`src/orchestrator/config.py`) loads `config/<env>.yml`, then applies environment-variable overrides for:
  - `SCRAPY_CONCURRENT_REQUESTS`
  - `SCRAPY_DOWNLOAD_DELAY`
  - `STAGE1_MAX_DEPTH`
  - `STAGE1_BATCH_SIZE`
- YAML files define Scrapy settings, stage outputs, and canonical data directories. The orchestrator creates directories on startup (`data/raw`, `data/processed`, `data/logs`, etc.).

## Data Outputs
- Stage 1: `data/processed/stage01/new_urls.jsonl`
- Stage 2: `data/processed/stage02/validated_urls.jsonl`
- Stage 3: `data/processed/stage03/enriched_data.jsonl` (when enrichment runs)
- Logs: `data/logs/pipeline.log` (+ console output)

## Project Layout
```
.
├── main.py
├── config/
│   ├── development.yml
│   └── production.yml
├── src/
│   ├── orchestrator/
│   ├── stage1/
│   ├── stage2/
│   ├── stage3/
│   └── common/
├── data/
│   ├── raw/
│   ├── processed/
│   ├── catalog/
│   ├── cache/
│   ├── exports/
│   └── logs/
├── tests/
└── requirements.txt
```

## Testing
Run the test suite from the project root with:
```bash
python -m pytest
```
Common variants:
```bash
# Verbose output (default via pytest.ini)
python -m pytest -v

# Specific module
python -m pytest tests/utils/test_url_helpers.py

# Marked subsets (e.g., unit tests once they are implemented)
python -m pytest -m unit
```

Running `python -m pytest` executes 104 sample-driven tests that cover the discovery/validation/enrichment stages, shared utilities, and supporting storage/logging helpers without touching external services.

## Known Issues & TODOs
- **Stage 3 orchestration bug:** `run_concurrent_stage3_enrichment` references `urls_for_enrichment` before assignment, causing CLI execution to crash (`src/orchestrator/pipeline.py`).
- **Discovery dedup scalability:** dedupe state is kept in memory and rebuilt by scanning the entire JSONL file on spider start (`src/stage1/discovery_pipeline.py` comments).
- **Validation/enrichment test fidelity:** the new sample-driven tests mock network calls; add live integration coverage before production crawls.
- **Optional dependencies:** enrichment features relying on HuggingFace silently downgrade if models are absent; document and test the fallback behaviour once those dependencies are required in production.

## Future Improvements & Test Coverage Roadmap
1. Implement end-to-end Stage 1→Stage 2 integration tests using sample JSONL fixtures.
2. Add asynchronous tests for `PipelineOrchestrator.run_concurrent_stage2_validation` covering queue backpressure scenarios.
3. Finish `tests/utils/test_nlp_helpers.py` with both spaCy-present and spaCy-missing paths.
4. Create mocked HTTP responses for Stage 2 validator to verify HEAD→GET fallback logic deterministically.
5. Build regression tests for `common.urls.canonicalize_and_hash` to cover tricky query-string canonicalisation.
6. Add unit tests for `common.storage.JSONLStorage.append_batch` to ensure ordering and newline handling.
7. Write smoke tests that exercise `main.py` CLI argument parsing and `--config-only` output.
8. Cover Scrapy spider settings with integration tests using the Scrapy `CrawlerRunner` test harness.
9. Implement property-based tests for deduplication in `Stage1Pipeline` to catch hash collisions or state-reset bugs.
10. Add concurrency stress tests for `BatchQueue.get_batch_or_wait` with simulated producer/consumer tasks.
11. Create fixtures for Stage 3 enrichment to validate entity/keyword extraction with and without optional dependencies installed.
12. Develop snapshot tests for the JSONL format emitted by each stage to prevent schema regressions.
13. Add tests ensuring environment-variable overrides in `Config` update nested YAML keys correctly.
14. Implement coverage for logging configuration to ensure rotating file handlers are created as expected.
15. Introduce contract tests for the data directory bootstrap logic to guarantee required folders exist before runs.
16. Validate error handling paths when Stage 1 encounters malformed CSV rows in the seed list.
17. Test graceful shutdown behaviour for the orchestrator when tasks raise exceptions mid-execution.
18. Add benchmark-style tests (marked `slow`) to observe performance impacts of large queues in validation.
19. Verify that Stage 3 manual Scrapy invocation works via a dedicated integration test once the orchestrator bug is fixed.
20. Track code quality by integrating linting (`ruff`/`flake8`) and typing (`mypy`) into the CI pipeline, with accompanying tests to ensure configs stay in sync.
