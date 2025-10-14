# Test Architecture Guide

This guide complements `tests/test_plan.csv` and explains how we organise, execute, and extend the pytest matrix for the scraping pipeline.

## Test Matrix & IDs
- **Authoritative catalog** – `tests/test_plan.csv` lists every committed and planned test, grouped by type (`unit`, `component`, `integration`, `contract`, `perf`).
- **ID convention** – `<TYPE>-<SUBSYSTEM>-<NNN>`, e.g. `UT-SCOUT-001`. Match the ID in test markers (via `pytest.mark.test_id`) when adding new cases.
- **Traceability** – Each CSV row records fixtures, key inputs, and expected outcomes; keep the README brief and defer detailed data to the CSV.

## Suite Layout
- `tests/unit/` – Fast checks for pure functions, spiders, and CLI entry points.
- `tests/component/` – Behaviour across a handful of collaborators (e.g. Stage 1 depth scheduler against mocked Redis).
- `tests/integration/` – Cross-stage flows using real implementations but isolated dependencies (see `tests/test_core_functionality.py`).
- `tests/contract/` – Schema assertions such as `stage1_to_stage2` message validation (planned; see CSV entry `CT-PIPE-001`).
- `tests/perf/` – Guardrails for throughput numbers; currently smoke-level (`PF-SCOUT-001`) ensures Scout triage stays under 250 ms/link.

## Execution Recipes
- **Everything**: `make test` (maps to `pytest -v`)
- **Unit only**: `make test-unit`
- **Integration**: `make test-integration` (expects local Redis/Postgres or docker-compose)
- **Tagged by ID**: `pytest -k "UT-SCOUT-001" -vv`
- **Performance smoke**: `pytest -m perf --durations=10`
- **With coverage**: `make test-coverage`

CI runs the lint, unit, and integration targets defined in the Makefile. Perf jobs can be scheduled via a workflow dispatch or local invocation (`pytest -m perf`).

## Fixtures & Helpers
- Central fixtures live in `tests/conftest.py` (environment bootstrapping) and bespoke fixtures sit next to their modules.
- Reusable mocked infrastructure (Delta Lake, Redis, easyocr) is defined under `tests/common/fixtures.py` (planned via CSV entry `UT-COMMON-004`).
- Snapshot data belongs in `tests/data/<subsystem>/` to keep tests hermetic.

## Adding or Updating Tests
1. Reserve an ID in `tests/test_plan.csv`.
2. Create/extend the test file; add `@pytest.mark.test_id("<ID>")`.
3. Update fixtures or data references here if new dependencies are introduced.
4. Run the appropriate command from **Execution Recipes** and capture artefacts if required (see workflow notes).

## Coverage Targets
- Stage 1 link triage ≥90 % branch coverage on `_process_discovered_urls`.
- Stage 2 analysis ≥85 % line coverage across `intelligent_analyzer.py`.
- Contract layer must assert all required fields in `specs/contracts/stage1_to_stage2.yaml`.

Current coverage snapshot (2024-11-05) from `pytest --cov`:
```
src/common/delta_lake.py      86%
src/stage1/base_spider.py     71%
src/stage2/stage2_worker.py   69%
overall                      74%
```
Update this block whenever coverage moves ≥5 %.

## Tooling & Automation
- **Pre-commit**: Hooks live in `.pre-commit-config.yaml`. Run `pre-commit run --all-files` before pushing or `make pre-commit`.
- **GitHub Actions**: `.github/workflows/ci.yml` mirrors the Makefile targets (lint → unit → integration) and uploads coverage artifacts per Python version.
- **Makefile**: Reference `make help` for the full catalogue of developer commands (linting, security scans, Docker helpers).

## Maintenance Cadence
- Review the CSV + README every sprint.
- Retire IDs rather than reusing them; superseded cases should reference their replacement in the notes column.
- Keep perf thresholds aligned with production budgets (documented in monitoring dashboards).
