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
├── migrate_data_paths.py       # Utility for migrating legacy data layouts
├── requirements.txt            # Default deps (core stack, Python 3.12)
├── requirements-dev.txt        # Dev/test extras
├── requirements-py312.txt      # Alternate pin set for 3.12 audit
└── PYTHON_312_*.md             # Migration audit and summary notes
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
   - Results are serialised to `data/processed/stage02/validated_urls.jsonl` with latency, status code, and content metadata.
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

# Stage 3 workaround (until orchestrator bug is resolved)
python -m scrapy crawl enrichment \
  -s STAGE3_OUTPUT_FILE=data/processed/stage03/enriched_data.jsonl \
  -a urls_file=data/processed/stage02/validated_urls.jsonl

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
- **Logging & observability:** Plan to emit structured JSON logs (`URL_DISCOVERED`, `DYNAMIC_ENDPOINT_FOUND`, batch checkpoints) and expose per-heuristic counters so operators can trace throughput spikes.
- **Efficiency measures:** Introduce adaptive crawl delays based on response latency, shared dedupe storage for parallel crawlers, and resumable checkpoints to minimise rework on restarts.
- **Faculty coverage:** Upcoming work will map every faculty profile and cross-link with RateMyProfessor; see the new plan below.
- **Logging & observability:** Plan to emit structured JSON logs (`URL_DISCOVERED`, `DYNAMIC_ENDPOINT_FOUND`, batch checkpoints) and expose per-heuristic counters so operators can trace throughput spikes.
- **Efficiency measures:** Introduce adaptive crawl delays based on response latency, shared dedupe storage for parallel crawlers, and resumable checkpoints to minimise rework on restarts.
- **Faculty coverage:** Upcoming Stage 1 work will map each faculty profile using registrar/department directories, then cross-link institutional data with third-party sources such as RateMyProfessor (summarised below).

## Requirements & Optional Extras

- `requirements.txt` includes Scrapy, aiohttp, Twisted, PyYAML, pytest, psutil, and spaCy.
- Optional NLP enhancements require `sentence-transformers`, `transformers`, and `huggingface-hub`.
- After installing requirements, download the spaCy model:
  ```bash
  python -m spacy download en_core_web_sm
  ```
- When running enrichment on resource-constrained machines, skip optional packages or disable the HuggingFace scoring path.

## Known Issues

- **Stage 3 CLI execution** – `PipelineOrchestrator.run_concurrent_stage3_enrichment` still references an undefined `urls_for_enrichment`; run Stage 3 through Scrapy directly until patched.
- **Stage 1 dedupe scalability** – JSONL rewind is O(n) on restarts; the roadmap tracks migration to a persistent hash index.
- **Limited live validation coverage** – Tests primarily use mocked responses. Add opt-in integration runs before production crawls.

## Faculty & RateMyProfessor Data Plan

1. **Faculty roster acquisition**
   - Expand Stage 1 seeds with registrar/department/lab directories so every profile URL is reachable.
   - Store canonical faculty records (`name`, `department`, `profile_url`, `discovery_source`) in JSONL/SQLite for reuse across runs.
   - Normalise name formats (e.g., `First M. Last`) to support external matching.

2. **Cross-linking sources**
   - Extract structured attributes from profile pages (email, phone, research areas) during Stage 3 enrichment to improve match confidence.
   - Generate embeddings for biography text to cluster faculty by discipline and flag missing departments.

3. **RateMyProfessor integration**
   - Build a separate fetcher (respecting ToS) to query RateMyProfessor by university + faculty name.
   - Use fuzzy matching (Levenshtein, e-mail, department cues) to associate RateMyProfessor entries with internal records.
   - Persist aggregated ratings/tags/comments with provenance flags so downstream consumers know the source.

4. **Ethics & compliance**
   - Honour RateMyProfessor access rules; consider API partnerships or manual exports where automation is restricted.
   - Provide opt-out mechanisms and flag sensitive data for manual review.

5. **Logging & monitoring**
   - Emit dedicated log events (`FACULTY_PROFILE_DISCOVERED`, `RMP_MATCHED`) and track per-department coverage in dashboards.
   - Add summary reports comparing discovered faculty profiles vs expected counts from institutional rosters.

## Faculty & RateMyProfessor Data Plan

1. **Faculty roster acquisition**
   - Stage 1 will expand seeds to include registrar, department, and lab directories, ensuring every faculty profile URL is discovered.
   - Store faculty records with metadata (`name`, `department`, `profile_url`, `discovery_source`) in a dedicated JSONL/SQLite table for reuse.
   - Normalise naming conventions (e.g., `First M. Last`) to support matching against external datasets.

2. **Cross-linking sources**
   - Extract email addresses, phone numbers, and research areas from faculty profiles to increase matching confidence.
   - In Stage 3 enrichment, compute embeddings for biography text to cluster by discipline.

3. **RateMyProfessor integration**
   - Maintain a separate fetcher (outside the main crawl if ToS requires) that queries RateMyProfessor by university and faculty name.
   - Use fuzzy matching (Levenshtein distance, email/department cues) to associate RateMyProfessor records with the internal faculty catalog.
   - Store RateMyProfessor metadata (ratings, tags, comments summary) alongside institutional data with provenance flags.

4. **Ethics & compliance**
   - Review Terms of Service for RateMyProfessor and abide by access limits; consider manual or API-based exports where available.
   - Provide opt-out support and flag sensitive data for manual review.

5. **Ongoing logging**
   - Add dedicated log events (`FACULTY_PROFILE_DISCOVERED`, `RMP_MATCHED`) with counts per department.
   - Surface summary dashboards showing coverage (profiles discovered vs expected) and matching success rates.

## Contributing & Change Ideas

- Use feature flags for new discovery heuristics to throttle high-churn areas without removing coverage.
- Add smoke tests for `run_tests.py --smoke` once the stabilisation workstream lands.
- Document schema versions and publish manifests under `data/catalog/` to keep downstream consumers aligned.
- Consider splitting optional dependencies into extras (`pip install .[enrichment]`) once packaging is added.
