# CI Testing Stages

These notes describe how our GitHub Actions workflows orchestrate linting and pytest jobs, and what each stage must produce.

## Stage Order
- **Lint** – `ruff check .` (cached via `~/.cache/ruff`). Fails fast; populates annotations. Uploads `lint-report.txt` for triage.
- **Unit** – `pytest tests/unit --maxfail=1 -q`. Uses Python dependency cache keyed by `poetry.lock` / `requirements.txt`. Produces `unit-junit.xml`.
- **Component** – `pytest tests -m "component" -vv`. Requires Redis & Postgres services (Docker) when available; falls back to fakes. Upload artefacts: `component-junit.xml`, `coverage-unit.xml`.
- **Integration** – `pytest tests/test_core_functionality.py --cov=src --cov-report=xml --cov-report=term`. Needs Delta Lake temp dir on runner (`/tmp/delta`). Saves `integration-junit.xml`, `coverage.xml`, and `htmlcov/`.
- **Performance (nightly only)** – `pytest -m perf --durations=10`. Threshold driven by `PF-SCOUT-001`. Artefacts: `perf-benchmark.json`.

Each stage must respect the `PYTEST_ADDOPTS` environment exported by the workflow to keep log verbosity consistent.

## Cache Hints
- **Python deps** – Cache `~/.cache/pip` with key `pip-${{ hashFiles('requirements.txt', 'dev-requirements.txt') }}`.
- **Ruff** – Cache `~/.cache/ruff` keyed by `ruff-${{ hashFiles('pyproject.toml', 'ruff.toml') }}`.
- **Pytest** – Cache `.pytest_cache` only on integration + perf jobs to reuse node ids across retries.

## Artefacts to Publish
- `lint-report.txt`
- `unit-junit.xml`, `component-junit.xml`, `integration-junit.xml`
- `coverage.xml` + `htmlcov/` (integration stage)
- `perf-benchmark.json` (nightly perf)
- `logs/` directory when a stage fails (collect with `actions/upload-artifact`)

## Failure Handling
- Lint failure stops the pipeline.
- Unit/component failures block merge unless labelled `flake`. Retries allowed once via workflow dispatch.
- Integration failures produce a Slack notification with link to artefacts.
- Perf regressions warn but do not block PRs; the nightly job opens an issue if thresholds exceed 10 % for two consecutive runs.
