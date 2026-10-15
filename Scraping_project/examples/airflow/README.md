# Example Airflow DAG

Optional scheduler for the scraping pipeline. It is **not** a Docker Compose
service, **not** in `requirements.txt`, and **not** started by `python start.py`.
The core profile (redis, scraper, stage 1–3 workers, Postgres) is unchanged.
Airflow runs somewhere else and shells out to the CLI that already ships.

`PipelineOrchestrator` is process-local. This DAG does not embed it in
Compose. Each task is one shot and then exits. Do not schedule
`python -m src.workers.stageN_worker`: those are the Compose loops.

## What you need

- Airflow **2.4 or newer** on the scheduler and the worker that runs the tasks
  (not installed by this repo).
- Both files, kept side by side, on the Airflow dags path:
  - `scraping_pipeline_dag.py`
  - `stage_env.py`
- A checkout of `Scraping_project/` on that worker, with the project venv.
  Stage 4 also needs the ML extra, and only if you turn it on.
- Redis reachable from the worker. Postgres and Kafka are optional, same as
  a CLI run without those services.

`schedule` is `None`, so the DAG does not crawl until someone triggers
`scraping_pipeline`. To run it on a clock, set `schedule` yourself (for
example `0 6 * * *`). Retries stay 0: a failed stage is not re-fired
automatically.

Task order matches the orchestrator: stage 1, then stage 2, then stage 3
and stage 4 in parallel. Stage 4 is a no-op unless `pipeline_run_stage4` is
true.

| Task | Command | Exits |
|---|---|---|
| `stage1_discovery` | `python cli.py scrapy --spiders <pipeline_spider>` | when the spider finishes |
| `stage2_analysis` | `python cli.py pipeline --skip-stage1 --skip-stage3 --stage2-workers … --stage2-batch …` | after one stage 2 pass |
| `stage3_summarize` | `python cli.py pipeline --skip-stage1 --skip-stage2 --stage3-workers … --stage3-batch …` | after one stage 3 pass |
| `stage4_large_docs` | `PipelineOrchestrator().run_stage_by_name('stage4')` | after one stage 4 pass, or immediately when disabled |

`cli.py pipeline` has no stage 4 flag. The stage 4 task calls the orchestrator
once. It does not start the Compose `stage4-worker` service. A spider other
than `scout` gets `--experimental`, because `cli.py scrapy` refuses lab
spiders without that flag. `scout` is the supported production spider.

Working directory is `pipeline_project_dir` (the `Scraping_project/` tree that
contains `cli.py`). The child process environment is built from the
connections below. Passwords stay in the environment, not on the command line.

## Airflow Variables

Set these in Admin → Variables. Defaults live in `stage_env.py`
(`AIRFLOW_VARIABLES`) and apply when the variable is missing.

| Variable | Default | Effect |
|---|---|---|
| `pipeline_project_dir` | *(required, empty)* | Directory that contains `cli.py`. The task fails until this is set. |
| `pipeline_python` | `python3` | Interpreter. A path is fine. It must not start with `-`. |
| `pipeline_spider` | `scout` | Stage 1 spider name passed to `cli.py scrapy --spiders`. |
| `pipeline_stage2_workers` | `100` | `--stage2-workers`. Integer ≥ 1. |
| `pipeline_stage2_batch` | `200` | `--stage2-batch`. Integer ≥ 1. |
| `pipeline_stage3_workers` | `50` | `--stage3-workers`. Integer ≥ 1. |
| `pipeline_stage3_batch` | `100` | `--stage3-batch`. Integer ≥ 1. |
| `pipeline_run_stage4` | `false` | `true`, `yes`, `on`, or `1` runs stage 4 once. Anything else skips it. Stage 4 is not on the core image. |
| `delta_lake_path` | `./data/delta_lake` | `DELTA_LAKE_PATH` for the child. Relative paths are relative to `pipeline_project_dir`. |
| `pipeline_log_level` | `INFO` | `LOG_LEVEL` for the child. |

## Airflow connections

Create these in Admin → Connections. The DAG reads them **inside the task**
(not at parse time).

### `scraping_redis` (required)

Connection type Redis (or generic). The host is required.

| Connection field | Child environment |
|---|---|
| Host | `REDIS_HOST` |
| Port (blank → 6379) | `REDIS_PORT` |
| Password (blank → unset, not an empty password) | `REDIS_PASSWORD` |
| Extra JSON `db`, else `database` (blank → `0`) | `REDIS_DB` |

Do not put a URL in the connection. The child environment **drops**
`REDIS_URL` even if the scheduler has one, so the four variables above are
the only Redis contract (#511).

Example extra: `{"db": 0}`.

### `scraping_postgres` (optional)

Missing connection: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` and
`DB_PASSWORD` are removed from the child, so it does not inherit the
scheduler's database. The CLI still runs (metrics logging degrades). If the
connection exists, host is required.

| Connection field | Child environment |
|---|---|
| Host | `DB_HOST` |
| Port (blank → 5432) | `DB_PORT` |
| Schema (blank → `scraping_pipeline`) | `DB_NAME` |
| Login (blank → `scrapy_app`) | `DB_USER` |
| Password | `DB_PASSWORD` |

Use the least-privilege `scrapy_app` role, not the Postgres superuser.

### `scraping_kafka` (optional)

Missing connection, or a blank host: `KAFKA_BOOTSTRAP_SERVERS` is removed
from the child, so it does not inherit the scheduler's broker. Kafka is the
Compose `streaming` profile, not `core`.

| Connection field | Child environment |
|---|---|
| Host already containing `:` (for example `kafka:9092`) | `KAFKA_BOOTSTRAP_SERVERS` as given |
| Host without a port, plus Port (blank → 9092) | `host:port` |

## What this does not do

- It does not add an `airflow` service, profile, or image to `docker-compose.yml`.
- It does not install `apache-airflow` or Prefect into the project lockfile.
- It does not change `PipelineOrchestrator`, `cli.py`, or the Compose worker commands.
- A Prefect flow is not included. The same CLI commands and the same
  environment variables are what a flow would call.
