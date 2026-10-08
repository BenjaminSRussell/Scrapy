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

## 6. Optional environment helpers

- **Python version:** `.python-version` (repo root) pins **3.11** for pyenv, mise, and
  asdf. 3.11 is the Dockerfile's interpreter and the primary CI leg. CI also tests
  **3.12** (`main.yml` matrix `["3.11", "3.12"]`), so code must run on both.
- **direnv** (optional): `cp .envrc.example .envrc && direnv allow` inside
  `Scraping_project/`. It activates `.venv`, exports `PYTHONPATH=.:src` (same as
  `pytest.ini`), and loads `.env` if present. `.envrc` is git-ignored. Keep secrets in
  `.env`, never in `.envrc.example`.
- **Dev container / Codespaces:** `.devcontainer/` starts Python 3.11 with a Redis
  service (`REDIS_HOST=redis`), opens in `Scraping_project/`, and installs the CI pins
  plus the pre-commit hook. In Codespaces use **Code → Codespaces → Create**. In VS Code
  use **Dev Containers: Reopen in Container**. Then run the step 4 pytest line. Kafka and
  Postgres are not included (tests that need them skip). Use `docker-compose.yml` for
  the full stack.

## Running Scrapy directly

`Scraping_project/scrapy.cfg` sets `[settings] default = src.settings`, and Scrapy only
finds it when you run from `Scraping_project/` (or below):

```bash
cd Scraping_project
scrapy list          # base, deep_dive, depth, javascript, scout
scrapy crawl scout
```

Run from the repo root and Scrapy reports *no active project*. `project = uconn_scraper`
in the `[deploy]` section is only the **scrapyd** project name. It is unrelated to
`BOT_NAME` (from `config.yml` `bot_name`, default `uconn_scraper`, used in the default
User-Agent and stats). **Scrapyd deploy is unsupported**: the `url` is commented out
and nothing in CI or Helm uses it. The supported runners are `start.py`, `cli.py`, and
the Helm chart.

## Releases, changelog, and decisions

- Add a line to [CHANGELOG.md](CHANGELOG.md) under `## [Unreleased]` for behaviour
  changes. The versioning policy and tag checklist are in [docs/RELEASING.md](docs/RELEASING.md).
- Record decisions that are expensive to reverse as ADRs in [docs/adr/](docs/adr/README.md).
- Report security issues privately, as described in [SECURITY.md](SECURITY.md). Everyone
  taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).
- Example scripts and their extra dependencies are listed in
  [Scraping_project/examples/README.md](Scraping_project/examples/README.md).

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
