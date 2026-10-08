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

## 6. Optional environment helpers

- **Python version:** `.python-version` (repo root) pins **3.11** for pyenv, mise, and
  asdf. 3.11 is the Dockerfile's interpreter and the primary CI leg. CI also tests
  **3.12** (`main.yml` matrix `["3.11", "3.12"]`), so code must run on both.
- **direnv** (optional): `cp .envrc.example .envrc && direnv allow` inside
  `Scraping_project/`. It activates `.venv`, exports `PYTHONPATH=.:src` (same as
  `pytest.ini`), and loads `.env` if present. `.envrc` is git-ignored. Keep secrets in
  `.env`, never in `.envrc.example`.
- **Redis in the dev container (#694):** the dev container (section 4b) includes
  docker-in-docker, so `docker compose up -d redis` from `Scraping_project/` starts the
  project's Redis on `localhost:6379` (the default `REDIS_HOST`). The unit subset does
  not need it: Redis-backed tests use fakeredis, and the smoke check only warns. Use
  the full `docker-compose.yml` for Kafka and Postgres.

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

## Operations CLI (`cli.py`)

`Scraping_project/cli.py` is the main ops tool (#345). Run `python cli.py --help`, or `make cli-help`, for the full list. Each subcommand also accepts `--help`.

| Command | What it does |
|---|---|
| `python cli.py health` | Row and file counts for every Delta table |
| `python cli.py validate` | Validate the Delta tables |
| `python cli.py seeds list\|add\|disable\|audit` | Manage seed URLs, with an audit log |
| `python cli.py scrapy --spiders scout` | Run Scrapy spiders |
| `python cli.py deep_dive` | Run the conservative deep-dive spider |
| `python cli.py pipeline [--skip-stage1 ...]` | Run stages 1–3 once, in order |
| `python cli.py export --table T --format csv\|json\|parquet` | Stream Delta tables to files |
| `python cli.py drain` | Clear the transient Redis queues (`drain_lake.py`); persistent queues such as Stage 4 are kept |
| `python cli.py queue-gc [--dry-run]` | Delete expired completed or failed rows from the stage queue tables |
| `python cli.py data gc --ttl-days 14` | TTL cleanup of logs, cache and temp artifacts |
| `python cli.py reset` | Reset Delta Lake and re-seed (guarded; asks for confirmation) |
| `python cli.py clean` | Remove temporary files |
| `python cli.py killswitch on\|off\|status\|audit` | Global crawl kill switch and budgets |
| `python cli.py ml review-export` | Export low-confidence ZSC records for review |
| `python cli.py setup` | Model setup placeholder |

Seeds can also be bulk-loaded with `python reseed.py --csv <file>`, which validates them. `scripts/load_seeds.py` is an older one-shot loader for `data/raw/uconn_urls.csv` that is kept for existing automation. It does not canonicalize URLs, so prefer `reseed.py` or `cli.py seeds add`. There is no `cli.py load_seeds`.

### Docker Compose v1 and v2

`start.py`, `shutdown.py`, the ops scripts and the Makefile work with either Compose CLI (#342):

- **`start.py` / `shutdown.py`** use `docker-compose` if it is installed, otherwise the `docker compose` plugin (see `compose_cli.py`). Set `COMPOSE_CMD="docker compose"` to choose explicitly.
- **Shell scripts** source `scripts/compose_lib.sh`, which prefers the plugin.
- **The Makefile** defaults to `COMPOSE ?= docker compose`. Override it with `make COMPOSE=docker-compose …`.

## Unsupported scripts

`temp_scripts/` (repo root) and `Scraping_project/temp_scripts/` hold one-off experiments
and are **not** supported entry points. Use `cli.py`, `start.py`, `shutdown.py` and
`scripts/` instead.

## Pull requests

Fill in [`.github/pull_request_template.md`](.github/pull_request_template.md). Put
`Closes #N` in the body, add tests with the fix, and keep CI green.
