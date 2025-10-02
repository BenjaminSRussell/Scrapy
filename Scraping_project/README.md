# UConn Web Scraping Pipeline

A three-stage scraping pipeline for the `uconn.edu` domain. Stage 1 discovers URLs (including dynamic/AJAX endpoints), Stage 2 validates their availability, and Stage 3 enriches content for downstream modelling. `main.py` is the CLI entry point and delegates to the asyncio orchestrator in `src/orchestrator/main.py`.

## Repository Map

```text
Scraping_project/
├── main.py                     # CLI entrypoint
├── config/                     # Environment-specific YAML settings
├── data/                       # Runtime artefacts (seeds, outputs, logs)
├── docs/                       # Supplementary documentation & roadmaps
├── src/
│   ├── common/                 # Shared helpers (logging, NLP, storage, URL utils)
│   ├── orchestrator/           # Async pipeline orchestration + queues
│   ├── stage1/                 # Discovery spider & pipeline
│   ├── stage2/                 # Async URL validator
│   └── stage3/                 # Enrichment spider & pipeline
├── tests/                      # Unit, integration, regression suites
└── requirements.txt            # Python dependencies (core + optional)
``` 

## End-to-End Data Flow

1. **Seeds & configuration**
   - Input seeds: `data/raw/uconn_urls.csv` (one URL per line, no header).
   - Runtime settings: `config/<env>.yml` plus optional env overrides (see below).

2. **Stage 1 – Discovery (`src/stage1`)**
   - `DiscoverySpider` consumes the seed CSV, canonicalises URLs, and walks the domain breadth-first.
   - Dynamic discovery heuristics scan data attributes, inline JSON, and scripts to surface AJAX endpoints and hidden APIs.
   - Unique findings are persisted via `Stage1Pipeline` to `data/processed/stage01/new_urls.jsonl` (newline-delimited JSON records).

3. **Stage 2 – Validation (`src/stage2`)**
   - `URLValidator` reads Stage 1 output, then performs concurrent HEAD→GET checks with `aiohttp`.
   - Results are serialised to `data/processed/stage02/validation_output.jsonl` with latency, status code, and content metadata.
   - The orchestrator’s `BatchQueue` keeps producers/consumers in lock-step so large batches avoid deadlock.

4. **Stage 3 – Enrichment (`src/stage3`)**
   - `EnrichmentSpider` pulls validated URLs (via queue or JSONL) and extracts title, body text, NLP entities/keywords, and flags for downloadable media.
   - Output is stored in `data/processed/stage03/enriched_data.jsonl` with schema suitable for fine-tuning or search indexing.

5. **Exports & monitoring**
   - Logs stream to stdout and, when configured, rotate under `data/logs/`.
   - `docs/pipeline_improvement_plan.md` captures current roadmap priorities and operational guidance.

> The orchestrator can run stages independently (`--stage 1`, `2`, `3`) or sequentially (`--stage all`). Stage 3 currently requires a manual workaround (see Known Issues).

## Inputs, Outputs, and Configuration

| Stage | Primary Input | Output JSONL Schema (key fields) | Notes |
|-------|---------------|-----------------------------------|-------|
| Stage 1 (Discovery) | `data/raw/uconn_urls.csv` | `source_url`, `discovered_url`, `first_seen`, `discovery_depth` | Respects depth limits, tracks dynamic/API URLs discovered. |
| Stage 2 (Validation) | Stage 1 JSONL | `url`, `url_hash`, `status_code`, `content_type`, `response_time`, `is_valid`, `error_message` | Uses HEAD with GET fallback; errors are captured as descriptive strings. |
| Stage 3 (Enrichment) | Stage 2 JSONL (valid URLs only) | `url`, `title`, `text_content`, `word_count`, `entities`, `keywords`, `content_tags`, `has_pdf_links`, `enriched_at` | Optional HuggingFace models add link scoring context. |

### Configuration files
- `config/development.yml` and `config/production.yml`: Scrapy tunables, concurrency, file paths, logging preferences.
- Environment variables override key values (`SCRAPY_CONCURRENT_REQUESTS`, `SCRAPY_DOWNLOAD_DELAY`, `STAGE1_MAX_DEPTH`, `STAGE1_BATCH_SIZE`).
- `requirements.txt` lists core dependencies; optional extras (Transformers, SentenceTransformers) enable advanced enrichment.

### Expected environment
- Python 3.8+
- Virtual environment recommended. Install dependencies and NLP models:
  ```bash
  pip install -r requirements.txt
  python -m spacy download en_core_web_sm
  ```

## Running the Pipeline

```bash
# Stage 1 only (discovery)
python main.py --env development --stage 1

# Stage 2 only (requires Stage 1 output)
python main.py --env development --stage 2

# Stage 3 only (requires Stage 2 output)
python main.py --env development --stage 3

# Run the full pipeline sequentially
python main.py --env development --stage all

# Inspect merged config without running stages
python main.py --env development --config-only
```

Useful CLI flags (`main.py --help`):
- `--stage {1,2,3,all}` – choose stages to execute.
- `--log-level` – override log verbosity (default `INFO`).
- `--config-only` – print resolved configuration.

## Testing Strategy

Run all tests:
```bash
python -m pytest
```

### Critical coverage
- `tests/integration/test_full_pipeline.py` – orchestrator queues and cross-stage wiring.
- `tests/stage2/test_validator_networking_regression.py` – retry/backoff behaviour and HEAD→GET fallbacks.
- `tests/pipelines/test_stage1_pipeline.py` – JSONL persistence, dedupe logic, and error handling for Stage 1 pipeline.
- `tests/stage3/test_enrichment_pipeline.py` – verifies enrichment schema and guarding against malformed inputs.

### Foundational/unit suites
- `tests/common/test_url_canonicalization_regression.py` – canonicalisation edge cases.
- `tests/common/test_storage.py` – JSONL/SQLite storage helpers.
- `tests/spiders/test_discovery_spider.py` – seed loading, link extraction, depth controls.
- `tests/common/test_nlp_integration_regression.py` & `tests/utils/test_nlp_helpers.py` – NLP registry behaviour and fallbacks.
- `tests/orchestrator/test_pipeline_orchestrator.py` – queue sizing, concurrent producer/consumer flow.

### Running subsets
- `python -m pytest tests/stage1` – focus on discovery logic.
- `python -m pytest -m integration` – run integration-tagged suites.
- `python -m pytest --maxfail=1` – stop on first failure during iterative development.

## Extensibility Notes & Future Direction

- **Dynamic discovery tuning:** Stage 1 now captures AJAX/API endpoints via heuristic scanning. Monitor the logged counters to decide where stricter throttles or paging heuristics (`TODO[stage1-ajax-interactions]`) should land.
- **Persistence & restartability:** Promote `common.storage.URLCache` to production to avoid rescanning large JSONL artefacts during restarts.
- **Stage 3 orchestration:** Fix the `urls_for_enrichment` reference and add smoke tests so CLI `--stage 3` once again works end-to-end.
- **Model-ready outputs:** Enrichment schema already houses text, entities, and tags; consider adding summarisation and provenance fields before training loops consume the data.
- **Operational playbooks:** See `docs/pipeline_improvement_plan.md` for prioritised roadmap tasks around batching, logging ergonomics, and schema validation.
- **Logging & observability:** Emit structured JSON logs (`URL_DISCOVERED`, `DYNAMIC_ENDPOINT_FOUND`, checkpoint syncs) and expose per-heuristic counters so operators can trace throughput spikes.
- **Efficiency measures:** Introduce adaptive crawl delays based on response latency, shared dedupe storage for parallel crawlers, and resumable checkpoints to minimise rework on restarts.
- **Faculty coverage:** Map faculty profiles and cross-link external sources (RateMyProfessor) as part of Stage 1/3 enrichment; see the plan below.

## Requirements & Optional Extras

- `requirements.txt` includes Scrapy, aiohttp, Twisted, PyYAML, pytest, psutil, and spaCy.
- Optional NLP enhancements require `sentence-transformers`, `transformers`, and `huggingface-hub`.
- After installing requirements, download the spaCy model:
  ```bash
  python -m spacy download en_core_web_sm
  ```
- When running enrichment on resource-constrained machines, skip optional packages or disable the HuggingFace scoring path.

### Technical Debt
- **Logging Format**: Logging is not yet standardized to a structured format (e.g., JSON) across all modules, making automated monitoring more difficult.
- **Overlapping Configuration**: The configuration system has too many overlapping options that need to be simplified and consolidated.
- **Error Masking**: Some `try...except` blocks may be too broad, potentially hiding important errors that should be addressed.

## Contributing & Change Ideas

1. Fork the repository.
2. Create a feature branch from `main` (`git checkout -b feature/your-feature-name`).
3. Make your changes and commit them with a descriptive message.
4. Run tests (`python -m pytest`) to ensure everything still works.
5. Submit a pull request for review.

### Future Project Directions
The following ideas focus on resilience, maintainability, and observability.

- **Dynamic Selector Hardening**: Improve heuristics for generating CSS/XPath selectors when layouts change, and add guard rails to detect stale locators early.
- **Rendering Diagnostics**: Capture lightweight screenshots or DOM snapshots during scraping runs to simplify debugging tricky, JavaScript-heavy pages.
- **Structured Data Pipelines**: Expand schema validation and catalog publishing so downstream consumers can rely on versioned, self-describing datasets.
- **Operational Tooling**: Build dashboards and alerting around checkpoint status, throughput, and retry rates to catch regressions quickly.

- Use feature flags for new discovery heuristics to throttle high-churn areas without removing coverage.
- Add smoke tests for `run_tests.py --smoke` once the stabilisation workstream lands.
- Document schema versions and publish manifests under `data/catalog/` to keep downstream consumers aligned.
- Consider splitting optional dependencies into extras (`pip install .[enrichment]`) once packaging is added.

### Branching and Development

When contributing, please follow these guidelines for branching.

#### Creating a New Branch
```bash
# Create and switch to a new feature branch from main
git checkout -b feature/your-feature-name

# Make your changes, then commit
git add .
git commit -m "feat: Add your descriptive feature summary"

# Push to the remote repository
git push -u origin feature/your-feature-name
```

#### Branch Naming Conventions
- `feature/` - New features or enhancements.
- `fix/` - Bug fixes.
- `docs/` - Documentation-only updates.
- `refactor/` - Code improvements without changing behavior.
- `test/` - Adding or improving tests.

## Faculty & RateMyProfessor Data Plan

1. **Faculty roster acquisition**
   - Expand Stage 1 seeds with registrar, department, and lab directories to guarantee every profile URL is discoverable.
   - Store canonical faculty records (`name`, `department`, `profile_url`, `discovery_source`) in JSONL/SQLite so later stages can reuse them without rescanning.
   - Normalise naming conventions (e.g., `First M. Last`) to improve matching with external datasets.

2. **Cross-linking sources**
   - Extract structured attributes (email, phone, research areas) during Stage 3 enrichment to strengthen match confidence.
   - Generate embeddings for biography text to cluster faculty by discipline and spot departments with missing coverage.

3. **RateMyProfessor integration**
   - Build a compliant fetcher that queries RateMyProfessor by university and faculty name (respecting ToS/rate limits).
   - Apply fuzzy matching (Levenshtein similarity, e-mail, department cues) to associate RateMyProfessor entries with internal records.
   - Persist aggregated ratings, tags, and comment summaries with provenance flags to keep downstream consumers aware of the data source.

4. **Ethics & compliance**
   - Honour RateMyProfessor access policies; prefer official exports or APIs when available.
   - Maintain opt-out capabilities and flag sensitive matches for manual review.

5. **Logging & monitoring**
   - Emit dedicated log events (`FACULTY_PROFILE_DISCOVERED`, `RMP_MATCHED`) and track per-department coverage in dashboards.
   - Publish summary reports comparing discovered faculty profiles with expected rosters to guide additional seed acquisition.

## Stage 3 Storage Configuration

Stage 3 now supports pluggable output backends via the `enrichment.storage` section in `config/*.yml`.
Example:

```yaml
stages:
  enrichment:
    output_file: data/processed/stage03/enrichment_output.jsonl
    storage:
      backend: jsonl
      options:
        path: data/processed/stage03/enrichment_output.jsonl
      rotation:
        max_items: 5000
      compression:
        codec: none
```

Available backends:

- `jsonl` (default) with optional rotation and gzip compression
- `sqlite` (persists rows in an `enrichment_items` table)
- `parquet` (requires `pyarrow`)
- `s3` (uploads JSONL batches; supports gzip compression and rotation thresholds)

The orchestrator propagates these settings to both the Scrapy pipeline and the async enrichment worker.

