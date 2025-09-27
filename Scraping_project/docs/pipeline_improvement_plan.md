# Scraping Project Pipeline Improvement Plan

## Current Strengths
- Clear three-stage architecture with an async orchestrator already capable of running stages independently via CLI flags.
- Config-driven data paths and logging hooks (`common.logging.setup_logging`) that can write both to console and rotating files.
- Discovery spider enforces canonicalisation and domain restrictions, providing a solid baseline for URL hygiene.
- Validation layer already async and batch-aware, with backpressure controls in `BatchQueue` to prevent queue overflows.
- Enrichment stack reuses shared NLP utilities, giving a head start on entity/keyword extraction and URL tagging.

## Key Risks and Gaps
- Stage 1 deduplication reloads the entire JSONL output on every run; this limits restarts and will not scale past hundreds of thousands of rows.
- Discovery currently treats all URLs equally: no keyword/section awareness, prioritisation, or provenance scoring to steer toward high-value academic content.
- Two separate `data/` trees exist (project root and repo root), making it easy to lose artefacts or run in inconsistent states.
- Stage 2 relies on `ValidationResult` but the dataclass lacks a `url_hash` field, so real runs crash once a validation result is instantiated with that attribute.
- Stage 3 orchestration is broken (`urls_for_enrichment` bug) and enrichment output does not yet include summaries/classifications suitable for model training.
- No persistent checkpointing: queue consumers cannot resume mid-batch, and partial JSONL writes make it hard to know what was processed during crashes.
- Test suite lacks coverage for stage-isolated execution, resumable workflows, and schema guarantees for the JSONL artefacts.

## Ranked Roadmap (highest priority first)
1. **Stabilise the current pipeline surface area**
   - Goal: remove the hard blockers that prevent today’s CLI stages from finishing successfully.
   - Actions: add `url_hash` to `common.schemas.ValidationResult`, repair the Stage 3 orchestrator bug (`urls_for_enrichment`), and backfill smoke tests to ensure the happy path (`--stage 1|2|all`) completes without exceptions before deeper changes begin.
   - Observability: introduce a lightweight `run_tests.py --smoke` target that exercises each stage in isolation and fails fast when schema mismatches or orchestration regressions slip in.

2. **Durable state, batching, and restartability across stages**
   - Goal: guarantee that each stage can resume after interruption without data loss or duplicate work.
   - Actions: wire `common.storage.URLCache` into every stage as the single source of truth, persist batch checkpoints (`stageN.checkpoint.json`) after each flush, and write idempotent consumers that skip URLs marked as completed. Implement batch-id metadata in JSONL lines plus a manifest file that records last successful batch.
   - Observability: add restart diagnostics (`--dry-run` flag to list pending batches) and console warnings when checkpoints diverge from JSONL contents.

3. **Stage 1 discovery overhaul for targeted URL growth**
   - Goal: optimise crawling toward university content slices (academics, research, services) while keeping discovery resumable and efficient.
   - Actions: introduce keyword/topic weighting on the frontier (priority queue seeded by CSV metadata), capture referring context, and write shallow content fingerprints per URL. Replace in-memory hash set with persistent index (SQLite/Bloom filter) so restarts skip already-seen URLs without scanning the JSONL history.
   - Observability: add console progress bars and structured counters (total/unique/keyword hits) emitted every N pages for standalone runs.

4. **Data architecture consolidation under `Scraping_project/`**
   - Goal: eliminate ambiguity about where raw/processed/log data lives.
   - Actions: migrate root-level `data/` and `logs/` into the project `data/` tree, update YAML configs + README, and add a bootstrap check that refuses to run if directories outside the project root still contain recent artefacts. Provide a migration script that copies legacy files once.
   - Observability: add a startup audit log summarising active paths and last modified timestamps so operators can verify the job is using the intended storage.

5. **Stage-scoped CLI + logging ergonomics**
   - Goal: let operators run `--stage 1|2|3` with rich console output and without hidden dependencies on other stages.
   - Actions: extend CLI to accept `--checkpoint`, `--resume`, and `--log-json` options; ensure `setup_logging` always attaches a console handler even when log_dir is missing. Provide per-stage log prefixes, and document run flows in README.
   - Observability: introduce structured log events (JSONL or key=value) so tailing `pipeline.log` or stdout provides actionable status.

6. **Model-ready JSONL schema and summarisation**
   - Goal: produce enrichment artefacts that include concise summaries, content-type labels, and metadata required for downstream training with minimal manipulation.
   - Actions: add summarisation step (extractive first, abstractive optional), capture page-level taxonomy labels (degree programs, research areas, services), attach provenance (source seed, discovery depth, validation outcomes) to each record, and version the schema (`schema_version` field). Document the schema in `docs/` and create fixtures that mirror the final format.
   - Observability: add validators that check JSONL lines against the documented schema before batches are marked complete.

7. **Test and monitoring expansion**
   - Goal: continuously verify that each stage works in isolation, resumes cleanly, and emits the contractually-defined datasets.
   - Actions: add unit tests for the new `URLCache` integration, queue checkpoint behaviour, and keyword-prioritised discovery ordering. Create snapshot tests for JSONL schemas, CLI smoke tests for `--stage` combinations, and integration tests that simulate crash/restart cycles. Wire in health metrics (counts, last processed timestamps) that can be surfaced via CLI or exported metrics.
   - Observability: configure stage-level test fixtures to emit coverage reports and add monitoring hooks (e.g., simple Prometheus-compatible text files) for batch success/failure counts.

## Supporting Processes
- **Operational playbooks:** document start/resume/abort flows per stage, including how to inspect checkpoints and how to rerun batches safely.
- **Data retention & versioning:** maintain `data/catalog/manifest.json` with pointers to every JSONL batch plus hashing for integrity checks; rotate obsolete batches to `data/exports/`.
- **Dependency management:** lock down optional NLP/HuggingFace dependencies with extras (`pip install .[enrichment]`) and add availability checks so enrichment fails fast when models are missing.
- **Review cadence:** adopt a weekly review of queue metrics, dedupe hit rates, and enrichment summarisation quality to keep the roadmap grounded in observed behaviour.
