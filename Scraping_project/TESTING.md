# Testing Guide

This document complements `tests/README.md` and describes how to run, extend, and automate the test suite end-to-end.

## Test Taxonomy

| Suite            | Location                  | Purpose                                              | Typical Runtime |
|------------------|---------------------------|------------------------------------------------------|-----------------|
| Unit             | `tests/unit/`             | Fast checks for isolated modules and utilities       | < 2 minutes     |
| Component        | `tests/component/`        | Validates behaviour across a few collaborators       | 2–5 minutes     |
| Integration      | `tests/integration/`      | Exercises cross-service flows (Redis/Postgres/Delta) | 5–8 minutes     |
| Contract         | `tests/contract/`         | Schema validation against published contracts        | < 1 minute      |
| Performance      | `tests/performance/`      | Guardrails for latency & throughput budgets          | ~5 minutes      |

Refer to `tests/test_plan.csv` for the authoritative catalog of cases and IDs.

## Running Tests Locally

```bash
# Install tooling
make install-dev

# Lint & unit tests
make quick-check

# Full suite
make test

# Focused runs
make test-unit
make test-integration
pytest -k "UT-SCOUT-003" -vv
```

Integration tests expect Redis and Postgres. Start them via Docker Compose or run `make docker-up`.

## Coverage

```
make test-coverage
open htmlcov/index.html
```

CI publishes `coverage.xml` per Python version as artifacts (see `.github/workflows/ci.yml`).

## Pre-commit Hooks

Install once:

```
pre-commit install
```

Run manually before pushing:

```
make pre-commit
```

Hooks include Ruff, Black, isort, mypy, and a manual `pytest tests/unit` stage.

## Continuous Integration

The GitHub Actions workflow (`ci.yml`) runs three jobs:

1. **Lint** – `make lint` + `pre-commit run --all-files`
2. **Unit** – `make test-unit` on Python 3.12 & 3.13, uploads coverage artifacts
3. **Integration** – `make test-integration` with Redis and Postgres service containers

The optional `main.yml` workflow executes a quick smoke (`make quick-check`) on pushes to `main`.

## Contributing New Tests

1. Reserve an ID in `tests/test_plan.csv`.
2. Write the test, add fixtures as required (see `tests/conftest.py`).
3. Update documentation/notes if the test introduces new behaviours or dependencies.
4. Run `make test-unit` or relevant target locally.
5. Ensure pre-commit hooks pass.

## Useful Targets

| Command              | Description                               |
|----------------------|-------------------------------------------|
| `make help`          | List all developer commands               |
| `make lint-fix`      | Auto-fix formatting/lint issues           |
| `make security`      | Run `pip-audit` + `bandit`                |
| `make docker-test`   | Execute tests inside the Docker stack     |
| `make clean`         | Remove caches and coverage artefacts      |

Keep this document updated whenever the testing strategy evolves.
