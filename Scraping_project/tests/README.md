# Test suite

Run everything from `Scraping_project/` (`pytest.ini` sets `pythonpath = . src` and
`testpaths = tests`).

## What CI runs

```bash
python -m pytest tests/ -m "not slow and not kafka and not performance" -o addopts=
# same thing: make test-ci   (or ./run_all_tests.sh)
```

`-o addopts=` clears the project defaults. `pytest.ini`'s `addopts` turn on coverage
(`--cov=src`, HTML/XML/term reports) and `-n auto` (pytest-xdist). These are handy
locally but slow and noisy for quick loops, and CI doesn't use them.

## Fast local loops

```bash
python -m pytest tests/unit -m "not slow" -o addopts= -q    # unit tests, offline
python -m pytest tests/unit/stage2 -m unit -o addopts= -q   # one component
python -m pytest tests/ -k redis -o addopts= -q             # by keyword
make test-fast                                              # not slow / not performance
```

## Layout

| Path | What |
|---|---|
| `unit/` | Offline unit tests, grouped by component (`stage1/`…`stage4/`, `common/`, `core/`, `monitoring/`) |
| `component/`, `contract/` | Component behaviour and data-contract tests (item/record schemas) |
| `delta/` | Delta Lake read/write against a temp directory |
| `integration/` | Multi-component flows. Some use Redis/Postgres when reachable |
| `kafka/` | Kafka producer/consumer tests (`kafka` marker; skipped in PR CI) |
| `observability/` | Prometheus config, alert rules and metric-name checks (offline) |
| `performance/` | Load/stress benchmarks (`performance` + `slow`; `make test-perf`) |
| `retry/`, `test_*.py` | Retry/circuit breaker, cache, models, Redis smoke |
| `fixtures/` | HTML and data fixtures |
| `conftest.py` | Shared fixtures: `redis_clean` (FakeRedis), `delta_sandbox`, `postgres_clean`, `http_server`, … |

## Markers (`pytest.ini`, `--strict-markers`)

`unit`, `integration`, `slow`, `redis`, `postgres`, `kafka`, `scrapy`, `stage1`–`stage4`,
`smoke`, `security`, `performance`, `critical`, `component`, `contract`.

- **Skip Kafka:** `-m "not kafka"`. The Kafka suites need a broker and run in their
  own workflow.
- **Skip Redis/Postgres:** most Redis tests use FakeRedis through `redis_clean`, so they
  run anywhere. Tests that need a real server are marked `redis` or `postgres`; deselect
  them with `-m "not redis and not postgres"`. The `postgres_clean` fixture skips (it
  doesn't fail) when no Postgres is reachable.
- **Load tests:** `performance` and `slow` are excluded from PR CI. Run them with
  `make test-perf` (`pytest tests/performance -m performance`).

## Environment

- `OBS_OFFLINE=1` is set for every run by `pytest.ini` (`env = OBS_OFFLINE=1`) and
  exported by CI. It keeps observability code from opening network exporters.
- CI exports `REDIS_HOST/PORT` and `DB_*` for its service containers. Locally, leave
  them unset unless you run those services.

## Parallel runs (pytest-xdist) and filesystem isolation

`pytest-xdist` is pinned in `dev-requirements.txt`. The supported parallel invocation
is the CI command plus `-n`:

```bash
python -m pytest tests/ -m "not slow and not kafka and not performance" -o addopts= -n auto
```

`tests/conftest.py` gives every pytest process (each xdist worker is one) a private
temp root, and points `DELTA_LAKE_PATH` and `DLQ_PATH` into it unless you set them.
Code that falls back to the default lake or DLQ therefore never touches
`Scraping_project/data/`, and workers never share a Delta table. The run **fails** if
any file appears under `data/delta_lake`, `data/dlq` or `data/kafka_spill` during the
session. Use `tmp_path` (unique per test) for anything else you write.
`tests/unit/test_xdist_isolation_683.py` checks that isolation; CI runs the suite serially.

## Coverage

Coverage is collected through `addopts` (`--cov=src --cov-branch`) when you run plain
`pytest`. CI uploads `coverage.xml` as an artifact and does **not** enforce a threshold
(`--cov-fail-under=0`). `make test-coverage` writes `htmlcov/`.

## Writing tests

- Put new tests under the matching `unit/<component>/` directory. Everything under
  `unit/stage2/` gets the `unit` and `stage2` markers from its `conftest.py`.
- Keep tests offline. Use FakeRedis, `tmp_path` Delta tables and fakes like those in
  `unit/stage2/`.
- Heavy or long tests must carry `@pytest.mark.slow` or `performance`;
  `tests/unit/test_marker_policy.py` guards the performance suite.
