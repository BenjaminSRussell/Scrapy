
<div align="center">
  <img src="https://img.shields.io/badge/UConn%20Pipeline-Discovery-Validation-Enrichment-05437C?style=for-the-badge" alt="Pipeline badge" />
  <h1>UConn Web Scraping Pipeline</h1>
  <p><strong>Discover.</strong> <strong>Validate.</strong> <strong>Enrich.</strong><br/>An asyncio-driven data platform purpose-built for the University of Connecticut digital ecosystem.</p>
  <p>
    <img src="https://img.shields.io/badge/python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python" />
    <img src="https://img.shields.io/badge/aiohttp-ready-2E6F95?style=for-the-badge" alt="aiohttp" />
    <img src="https://img.shields.io/badge/integration-tested-2E8B57?style=for-the-badge" alt="Tested" />
  </p>
</div>

---

## Table of Contents
- [Why This Pipeline](#why-this-pipeline)
- [System Architecture](#system-architecture)
- [Repository Walkthrough](#repository-walkthrough)
- [Stage Deep Dive](#stage-deep-dive)
- [Configuration Quick Reference](#configuration-quick-reference)
- [Operating the Pipeline](#operating-the-pipeline)
- [Quality, Observability, and Resilience](#quality-observability-and-resilience)
- [Testing Strategy](#testing-strategy)
- [Contribution Guide](#contribution-guide)
- [Roadmap Highlights](#roadmap-highlights)
- [Design Language and Visual Identity](#design-language-and-visual-identity)

---

## Why This Pipeline
> Engineered for reliability, tuned for maintainability, and crafted to streamline every downstream consumer.

- **Delivers purpose-built discovery** for the University of Connecticut domain, with heuristics for hidden APIs and dynamic assets.
- **Delivers deterministic validation** leveraging <code>aiohttp</code> connection pooling, precise timeout measurement, and Content-Length governance.
- **Delivers a rich enrichment layer** producing schema-validated JSONL assets ready for search, analytics, or ML ingestion.
- **Delivers operational empathy** through checkpointing, back-pressure aware queues, and structured metadata at every hop.

---

## System Architecture
`mermaid
flowchart LR
  A[Seeds & Config] -- stage1 --> B[Discovery Spider]
  B -- new URLs --> C[Stage 1 Output JSONL]
  C -- stage2 --> D[Async URL Validator]
  D -- verdicts --> E[Stage 2 Output JSONL]
  E -- stage3 --> F[Enrichment Workers]
  F -- structured records --> G[Downstream Catalogs]
  D -. telemetry .-> H[Checkpoint Manager]
  F -. feedback .-> I[Adaptive Depth Manager]
  B -. link graph .-> J[Link Graph Analyzer]
`

**Orchestration core:** src/orchestrator/main.py stitches each stage together with async queues, checkpoint resumption, and environment-aware configuration.

---

## Repository Walkthrough

| Path | Description |
|------|-------------|
| main.py | CLI front door that resolves configuration, selects stages, and invokes the orchestrator. |
| config/ | Environment profiles (development.yml, production.yml) plus overrides via environment variables. |
| data/ | Seeds, checkpoint snapshots, processed outputs, and diagnostics. |
| docs/ | Design notes, validation matrices, and operational runbooks. |
| src/common/ | Shared utilities (schemas, checkpoints, adaptive depth, link graph analysis, metrics exporters). |
| src/stage1/ | Discovery spiders, prioritisation heuristics, and persistence pipeline. |
| src/stage2/ | Async validator featuring HEAD->GET fallback, precise content-length handling, and descriptive error capture. |
| src/stage3/ | Enrichment workflow with text extraction, entity tagging, and pluggable storage backends. |
| 	ests/ | Pytest suites covering unit, integration, networking regression, and orchestrator flows. |

---

## Stage Deep Dive

### Stage 1: Discovery (src/stage1)
- Breadth-first crawl seeded from data/raw/uconn_urls.csv.
- Dynamic heuristics inspect data attributes, inline JSON, and script tags to reveal AJAX endpoints.
- Deduplication and canonicalisation push clean records to data/processed/stage01/new_urls.jsonl.

### Stage 2: Validation (src/stage2)
- URLValidator batches URLs, honours HEAD responses when sufficient, and executes GET fallbacks with shared TCP connectors.
- Content-Length governance: honors well-formed headers and falls back to the actual payload size when they are not, keeping downstream metrics consistent.
- Exception handling preserves original iohttp error classes, yielding user-readable diagnostics while avoiding bare strings.
- Writes validation artefacts to data/processed/stage02/validation_output.jsonl with latency, status code, caching hints, and staleness scores.

### Stage 3: Enrichment (src/stage3)
- Pulls only validated URLs (status 2xx/3xx) and extracts structured content: titles, body text, keyword hints, media flags.
- Supports JSONL, SQLite, Parquet, or S3 outputs through the nrichment.storage configuration block.
- Emits adaptive feedback so discovery depth can be tuned based on observed content quality.

---

## Configuration Quick Reference

| Key | Description |
|-----|-------------|
| SCRAPY_CONCURRENT_REQUESTS | Overrides Scrapy concurrency (stage 1). |
| STAGE1_MAX_DEPTH | Caps traversal depth for discovery. |
| stage2.max_workers | Controls validator parallelism; automatically doubles for TCP connector limits. |
| stage2.timeout | Total request timeout fed into iohttp.ClientTimeout. |
| stage3.storage.backend | Output target (jsonl, sqlite, parquet, s3). |
| stage3.storage.rotation.max_items | Chunk size before starting a new artefact. |

> Merge order: environment YAML -> environment variables -> CLI overrides.

---

## Operating the Pipeline

`ash
# Activate your virtual environment, then:

# 1. Seed discovery only
python main.py --env development --stage 1

# 2. Validate previously discovered URLs
python main.py --env development --stage 2

# 3. Enrich validated pages
python main.py --env development --stage 3

# 4. Full sequential run
python main.py --env development --stage all

# 5. Inspect resolved configuration without execution
python main.py --env development --config-only
`

**Bootstrap checklist**
1. pip install -r requirements.txt
2. python -m spacy download en_core_web_sm (for enrichment NLP helpers)
3. Populate data/raw/uconn_urls.csv with seeds (no header)
4. Monitor console output (or log files) for checkpoint progress and status summaries

---

## Quality, Observability, and Resilience

- **Checkpointing:** src/common/checkpoints.py persists progress per stage – restart-friendly and resumable mid-batch.
- **Adaptive depth and feedback:** Stage 2 results feed adaptive depth logic so Stage 1 prioritises high-value paths.
- **Structured metadata:** Validation results capture cache headers, staleness, redirects, and response timing using 	ime.perf_counter for precision.
- **Content governance:** Schema validation via src/common/schemas_validated.py guarantees consumers receive predictable, contract-first payloads.
- **Monitoring hooks:** Prometheus exporter and enrichment storage emit metrics-friendly artefacts when enabled.

---

## Testing Strategy

`ash
python -m pytest                     # full test suite
python -m pytest tests/stage2 -k networking  # targeted validator regression tests
python -m pytest --maxfail=1 -q      # quick smoke
`

Coverage highlights:
- **Networking regressions:** 	ests/stage2/test_validator_networking_regression.py faithfully simulates aiohttp behavior through tailored mock sessions.
- **Orchestrator integration:** 	ests/integration/test_orchestrator_e2e.py validates multi-stage execution and checkpoint hand-offs.
- **Schema validation:** 	ests/common/test_schemas*.py guarantees JSONL contracts.

---

## Contribution Guide

1. Fork and branch from main (git checkout -b feature/your-feature).
2. Keep pull requests focused; update docs/tests alongside code.
3. Run pytest (and linting if configured) to verify a warning-free green build before opening a PR.
4. Document configuration toggles or migration notes in docs/ when relevant.
5. Submit a PR with context: goal, testing evidence, follow-up actions.

Branch naming suggestions: eature/*, ix/*, docs/*, 
efactor/*, 	est/*.

---

## Roadmap Highlights
- Dynamic selector hardening through heuristic scoring and smoke checks.
- Rendering diagnostics with optional DOM snapshots for JavaScript-heavy endpoints.
- Extended schema catalogues published under data/catalog/ for downstream discoverability.
- Operational dashboards for checkpoint success/failure, throughput, and retry rates.

---

## Design Language and Visual Identity
> A consistent design vocabulary keeps code, documentation, and UX aligned.

- **Color palette:** Navy (#05437C) plus Huskies blue (#2E6F95) with neutral accents for readability and UConn resonance.
- **Typography:** Prefer semantic Markdown headings, short paragraphs, and tables to optimise scan-ability on GitHub.
- **Iconography:** Shields.io badges highlight runtime guarantees (Python version, test posture) without overwhelming the reader.
- **Layout rhythm:** Section dividers (---) and callouts maintain visual pacing, guiding readers from overview to operations to contribution.

---

Happy scraping — see you in the next commit!
