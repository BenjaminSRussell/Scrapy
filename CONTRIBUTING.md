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
