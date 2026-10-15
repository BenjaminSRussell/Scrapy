"""Example Airflow DAG around the pipeline CLI. Not a Compose service.

Copy this file and ``stage_env.py`` together into the Airflow dags folder
(Airflow 2.4 or newer). They are not imported by the pipeline image, by
``requirements.txt``, or by ``docker-compose.yml``. Schedule stays unset:
trigger the DAG manually. Do not point a task at the Compose stage-worker
loops (they do not exit). Tasks call ``cli.py``, or one orchestrator shot
for stage 4.

Airflow Variables (defaults in ``stage_env.AIRFLOW_VARIABLES``):

- ``pipeline_project_dir`` — required path to ``Scraping_project/`` (no default)
- ``pipeline_python`` — interpreter (default ``python3``)
- ``pipeline_spider`` — stage 1 spider (default ``scout``)
- ``pipeline_stage2_workers`` — default ``100``
- ``pipeline_stage2_batch`` — default ``200``
- ``pipeline_stage3_workers`` — default ``50``
- ``pipeline_stage3_batch`` — default ``100``
- ``pipeline_run_stage4`` — default ``false`` (stage 4 is not on the core profile)
- ``delta_lake_path`` — default ``./data/delta_lake`` (``DELTA_LAKE_PATH``)
- ``pipeline_log_level`` — default ``INFO`` (``LOG_LEVEL``)

Airflow connections:

- ``scraping_redis`` — required. Host, port, password, and extra ``db``
  (or ``database``) become ``REDIS_HOST``, ``REDIS_PORT``, ``REDIS_PASSWORD``,
  ``REDIS_DB``. ``REDIS_URL`` is not set.
- ``scraping_postgres`` — optional. Host, port, schema, login, password become
  ``DB_HOST``, ``DB_PORT``, ``DB_NAME``, ``DB_USER``, ``DB_PASSWORD``.
- ``scraping_kafka`` — optional. Host and port become ``KAFKA_BOOTSTRAP_SERVERS``.
  Kafka is not part of the core Compose profile.

The task order matches ``PipelineOrchestrator``: stage 1, then stage 2, then
stage 3 and stage 4 together. Stage 4 runs only when ``pipeline_run_stage4``
is true, and then as one ``PipelineOrchestrator.run_stage_by_name('stage4')``
call. ``cli.py pipeline`` has no stage 4 flag.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

DAG_ID = "scraping_pipeline"


def _helpers():
    path = Path(__file__).resolve().with_name("stage_env.py")
    spec = importlib.util.spec_from_file_location("scraping_pipeline_stage_env", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}. Keep stage_env.py next to this DAG.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _variable(name: str, default: str) -> str:
    from airflow.models import Variable

    value = Variable.get(name, default_var=default)
    return "" if value is None else str(value)


def _connection(conn_id: str, *, required: bool) -> dict | None:
    from airflow.exceptions import AirflowNotFoundException
    from airflow.hooks.base import BaseHook

    try:
        conn = BaseHook.get_connection(conn_id)
    except AirflowNotFoundException:
        if required:
            raise
        return None
    extra = conn.extra_dejson if isinstance(getattr(conn, "extra_dejson", None), dict) else {}
    return {
        "host": conn.host,
        "port": conn.port,
        "password": conn.password,
        "schema": conn.schema,
        "login": conn.login,
        "extra": extra,
    }


def _interpreter(raw: str) -> str:
    value = raw.strip() or "python3"
    if value.startswith("-") or "\x00" in value:
        raise ValueError("Airflow Variable pipeline_python must be an interpreter path")
    return value


def run_stage(stage: str) -> None:
    """Run one CLI stage with the Airflow connection env. Picklable (module level)."""
    helpers = _helpers()
    variables = {name: _variable(name, default) for name, default in helpers.AIRFLOW_VARIABLES.items()}
    argv = helpers.stage_argv(stage, variables)
    if argv is None:
        return
    project = variables["pipeline_project_dir"].strip()
    if not project:
        raise RuntimeError(
            "Set Airflow Variable pipeline_project_dir to the Scraping_project directory "
            "(the directory that contains cli.py)."
        )
    env = helpers.stage_env(
        base=dict(os.environ),
        redis=_connection("scraping_redis", required=True),
        postgres=_connection("scraping_postgres", required=False),
        kafka=_connection("scraping_kafka", required=False),
        delta_lake_path=variables["delta_lake_path"],
        log_level=variables["pipeline_log_level"],
        worker_id=stage,
    )
    completed = subprocess.run(
        [_interpreter(variables["pipeline_python"]), *argv],
        cwd=project,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"{stage} exited {completed.returncode}")


def run_stage1() -> None:
    run_stage("stage1")


def run_stage2() -> None:
    run_stage("stage2")


def run_stage3() -> None:
    run_stage("stage3")


def run_stage4() -> None:
    run_stage("stage4")


with DAG(
    dag_id=DAG_ID,
    description="One-shot CLI stages for the scraping pipeline. Not part of Compose.",
    schedule=None,
    start_date=datetime(2024, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    tags=["example", "scraping-pipeline"],
    doc_md=__doc__,
    default_args={"owner": "scraping", "retries": 0},
) as dag:
    stage1 = PythonOperator(task_id="stage1_discovery", python_callable=run_stage1)
    stage2 = PythonOperator(task_id="stage2_analysis", python_callable=run_stage2)
    stage3 = PythonOperator(task_id="stage3_summarize", python_callable=run_stage3)
    stage4 = PythonOperator(task_id="stage4_large_docs", python_callable=run_stage4)
    stage1 >> stage2 >> [stage3, stage4]

# Airflow discovers the DAG object in module globals. The assert keeps the name used.
assert dag.dag_id == DAG_ID
