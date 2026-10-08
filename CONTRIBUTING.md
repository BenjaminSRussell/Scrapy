# Contributing

Everything except the GitHub workflows lives in **`Scraping_project/`**. Run every
command below from that directory unless it says otherwise; entry points, `pytest.ini`
(`pythonpath = . src`) and the Makefile all assume it.

## 1. Clone and create a virtualenv

```bash
git clone https://github.com/BenjaminSRussell/Scrapy.git
cd Scrapy/Scraping_project
python3.11 -m venv .venv          # CI runs 3.11 and 3.12
source .venv/bin/activate
```

## 2. Install (same pins as CI)

```bash
pip install -r requirements.txt -r dev-requirements.txt -r ci-tools.txt
```

`make install-dev` does the same and also installs the git hook (step 3).
JS-spider work also needs a browser: `playwright install chromium`.

## 3. Pre-commit hook

```bash
make install-dev
# or, by hand (the hook runs from the repo root, so point it at this directory's config):
pre-commit install --config "$(git rev-parse --show-prefix).pre-commit-config.yaml"
pre-commit run --all-files        # optional: run every hook once
```

## 4. Test, lint, type-check (what CI's `test` job runs)

```bash
python -m pytest tests/ -m "not slow and not kafka and not performance" -o addopts=
ruff check src/ --select F,E4,E7,E9
mypy src/ --config-file mypy.ini --ignore-missing-imports --no-strict-optional
bandit -r src/ -ll
node --test dashboard/tests/      # dashboard helpers (CI Dashboard workflow)
```

`./run_all_tests.sh` wraps the pytest line (pass a path to narrow it). Some tests use
Redis/Postgres when they are reachable; CI provides both as service containers.

## 4a. Quick local checks

- **Smoke check (#323).** `bash scripts/smoke_local.sh` checks the Python version, core imports and `src.settings`, plus optional Redis and Playwright (these only warn). It then runs a fast offline test subset and exits non-zero on any failure. `--no-tests` skips the pytest step; `SMOKE_TESTS="..."` picks a different subset.
- **CI matrix locally (#350).** `pip install nox`, then:
  - `nox` runs `lint` (ruff + mypy + bandit, the same gates as CI) and `tests` on every Python in the matrix that you have installed (3.11, 3.12).
  - `nox -s tests-3.12 -- tests/unit -x` runs one interpreter with extra pytest args.

  The matrix lives in `noxfile.py` (`PYTHONS`); a unit test keeps it equal to `.github/workflows/main.yml`.
- **Playwright (optional, #387).** You only need a browser for the JS spider and its tests. Everything else, including the default test set, runs without one:

  ```bash
  python -m playwright install chromium          # add --with-deps on a fresh Debian/Ubuntu box
  ```

## 4b. Editor and dev container

- **VS Code (#363).** `.vscode/launch.json` has debug configs for the current file, the current test file, all unit tests and `cli.py`. `.vscode/tasks.json` has tasks for unit tests, the CI default set, the CI lint gates, the smoke check and `start.py`. All of them run from `Scraping_project/` with `PYTHONPATH=.:src`.
- **Dev container / Codespaces (#321).** `.devcontainer/devcontainer.json` provides Python 3.11 with docker-in-docker (for `python start.py` / Compose), Rust (kafka-delta-ingest) and Node (dashboard tests). It opens in `Scraping_project/`, installs `requirements.txt`, `dev-requirements.txt` and `ci-tools.txt`, then runs the smoke check. Grafana (3000), Prometheus (9090) and the Control Center (8000) are forwarded.

## 5. Make targets

`make help` lists every target (tests, lint, Docker, docs). The common ones are
`make test-unit`, `make lint`, `make typecheck` and `make lock`.

## Dependencies (pip-tools)

The `.in` files are the source of truth; the `.txt` files are pip-compile lockfiles.

1. Edit `requirements.in` (runtime) or `dev-requirements.in` (tooling/tests).
2. From `Scraping_project/`, run `make lock`. This re-pins against the existing
   lockfiles (no upgrades) and writes relative paths in the generated header.
   Use `make update-deps` only when you mean to upgrade everything.
3. Commit the `.in` file and the regenerated `.txt` file together.

Never hand-edit pins without recompiling, and never commit a header with a
machine-local absolute path.

## Unsupported scripts

`temp_scripts/` (repo root) and `Scraping_project/temp_scripts/` hold one-off experiments
and are **not** supported entry points. Use `cli.py`, `start.py`, `shutdown.py` and
`scripts/` instead.

## Pull requests

Fill in [`.github/pull_request_template.md`](.github/pull_request_template.md). Put
`Closes #N` in the body, add tests with the fix, and keep CI green.
