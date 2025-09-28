# Scraping Project Pipeline Improvement Plan

## Current Strengths
- Clear three-stage architecture with an async orchestrator already capable of running stages independently via CLI flags.
- Config-driven data paths and logging hooks (`common.logging.setup_logging`) that can write both to console and rotating files.
- Discovery spider enforces canonicalisation and domain restrictions, providing a solid baseline for URL hygiene.
- Validation layer already async and batch-aware, with backpressure controls in `BatchQueue` to prevent queue overflows.
- Enrichment stack reuses shared NLP utilities, giving a head start on entity/keyword extraction and URL tagging.

## Key Risks and Gaps
- Stage 1 never persists `url_hash` or provenance information, so dedupe, lineage, and downstream joins fail; the hacked JSONL rewind still replays large files on restart.
- Stage 2 emits `url_hash` in practice but `ValidationResult` lacks the field, causing runtime `TypeError`s and blocking validation.
- Stage 3 orchestration still shells out to Scrapy; CLI `--stage 3` remains broken and enrichment writes default to an outdated filename.
- Discovery metrics/tests are out of sync (`unique_hashes_found` vs `unique_urls_found`), hiding crawl regressions.
- Dynamic discovery heuristics lack throttles or provenance logging, making it hard to tune noisy sources.
- Checkpointing/persistent queues are absent, so crashes or restarts replay work and risk duplicate output.
- Test coverage does not gate schema compatibility or cross-stage smoke runs, leaving regressions undetected.

## Ranked Roadmap

## Ranked Roadmap (highest priority first)

1. **Align schema + lineage across stages**
   - Add `url_hash` and provenance to Stage 1 output, propagate the field through `ValidationResult`, and ensure Stage 3 reads/writes the same schema.
   - Fix the discovery metrics/test mismatch so regression tests actually validate crawler coverage.
   - Deliver a `run_tests.py --smoke` profile that exercises `--stage 1|2|3` end-to-end.

2. **Repair Stage 3 orchestration**
   - Replace the subprocess Scrapy call with an internal consumer and reinstate CLI `--stage 3`.
   - Honour configured output paths (`enriched_data.jsonl`) and add structured enrichment counters/logs.

3. **Persistent dedupe & restartability**
   - Migrate Stage 1 dedupe to `URLCache`/SQLite or similar, introduce batch checkpoint manifests, and teach queues to resume without re-reading JSONL.
   - Surface restart diagnostics and alerts when checkpoints fall behind.

4. **Dynamic discovery observability**
   - Track `discovery_source`, add heuristic-level throttles, and expose counters so noisy AJAX endpoints can be tuned or suppressed quickly.

5. **Model-ready enrichment**
   - Once orchestration is stable, extend Stage 3 with summarisation/taxonomy tags and schema versioning documented in `docs/`.

6. **Faculty & RateMyProfessor data integration**
   - Implement the cross-linking plan from README: canonical faculty roster ingestion, fuzzy matching against external sources, and opt-out/provenance logging.

7. **Expanded tests & monitoring**
   - Add schema snapshot tests, queue stress tests, and health metrics (e.g., Prometheus scrapes or simple JSON stats) for each stage.

## Supporting Processes
- **Operational playbooks:** document start/resume/abort flows per stage, including how to inspect checkpoints and how to rerun batches safely.
- **Data retention & versioning:** maintain `data/catalog/manifest.json` with pointers to every JSONL batch plus hashing for integrity checks; rotate obsolete batches to `data/exports/`.
- **Dependency management:** lock down optional NLP/HuggingFace dependencies with extras (`pip install .[enrichment]`) and add availability checks so enrichment fails fast when models are missing.
- **Review cadence:** adopt a weekly review of queue metrics, dedupe hit rates, and enrichment summarisation quality to keep the roadmap grounded in observed behaviour.
