"""Example Airflow DAG stays documentation: CLI stages, documented variables, not Compose (#704)."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "airflow"

_LOCKFILES = (
    "requirements.in",
    "requirements.txt",
    "dev-requirements.in",
    "dev-requirements.txt",
    "pyproject.toml",
)


def _stage_env():
    path = EXAMPLE / "stage_env.py"
    spec = importlib.util.spec_from_file_location("scraping_pipeline_stage_env", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_variables_and_connections_are_documented():
    mod = _stage_env()
    readme = (EXAMPLE / "README.md").read_text(encoding="utf-8")
    parent = (ROOT / "examples" / "README.md").read_text(encoding="utf-8")
    dag = (EXAMPLE / "scraping_pipeline_dag.py").read_text(encoding="utf-8")
    for name in mod.AIRFLOW_VARIABLES:
        assert name in readme, name
        assert name in dag, name
    for name in mod.CONNECTIONS:
        assert name in readme, name
        assert name in dag, name
    assert "airflow/scraping_pipeline_dag.py" in parent
    assert "airflow/stage_env.py" in parent
    assert "compose" in parent.lower()
    assert "not" in parent.lower()


def test_stage_env_uses_redis_contract_not_url():
    mod = _stage_env()
    base = {"REDIS_URL": "redis://secret@wrong:1/9", "REDIS_PASSWORD": "stale", "PATH": "/usr/bin"}
    env = mod.stage_env(
        base=base,
        redis={"host": "redis", "port": 6380, "password": "p@ss", "extra": {"db": 2}},
        postgres={
            "host": "postgres",
            "port": 5432,
            "schema": "scraping_pipeline",
            "login": "scrapy_app",
            "password": "pw",
        },
        kafka={"host": "kafka", "port": 9092},
        delta_lake_path="/data/delta",
        log_level="INFO",
        worker_id="stage2",
    )
    assert env["REDIS_HOST"] == "redis"
    assert env["REDIS_PORT"] == "6380"
    assert env["REDIS_PASSWORD"] == "p@ss"
    assert env["REDIS_DB"] == "2"
    assert "REDIS_URL" not in env
    assert base["REDIS_URL"].startswith("redis://")  # the scheduler env is not mutated
    assert env["DB_HOST"] == "postgres"
    assert env["DB_PORT"] == "5432"
    assert env["DB_USER"] == "scrapy_app"
    assert env["DB_NAME"] == "scraping_pipeline"
    assert env["DB_PASSWORD"] == "pw"
    assert env["KAFKA_BOOTSTRAP_SERVERS"] == "kafka:9092"
    assert env["DELTA_LAKE_PATH"] == "/data/delta"
    assert env["WORKER_ID"] == "stage2"
    assert env["PATH"] == "/usr/bin"


def test_blank_redis_password_is_unset_and_host_is_required():
    mod = _stage_env()
    env = mod.stage_env(
        base={
            "REDIS_PASSWORD": "stale",
            "REDIS_URL": "redis://localhost:6379/0",
            "DB_HOST": "scheduler-db",
            "DB_PASSWORD": "scheduler",
            "KAFKA_BOOTSTRAP_SERVERS": "scheduler:9092",
        },
        redis={"host": "localhost", "port": None, "password": "", "extra": {}},
        delta_lake_path="./data/delta_lake",
    )
    assert env["REDIS_HOST"] == "localhost"
    assert env["REDIS_PORT"] == "6379"
    assert env["REDIS_DB"] == "0"
    assert "REDIS_PASSWORD" not in env
    assert "REDIS_URL" not in env
    assert "DB_HOST" not in env
    assert "KAFKA_BOOTSTRAP_SERVERS" not in env
    try:
        mod.stage_env(base={}, redis={"host": "  ", "port": 6379}, delta_lake_path="./data/delta_lake")
    except ValueError as exc:
        assert "scraping_redis" in str(exc)
    else:
        raise AssertionError("missing redis host must fail")


def test_kafka_host_with_port_is_not_doubled_and_db_alias_works():
    mod = _stage_env()
    env = mod.stage_env(
        base={},
        redis={"host": "redis", "port": 6379, "password": None, "extra": {"database": "1"}},
        kafka={"host": "broker:9093", "port": 9092},
        delta_lake_path="./data/delta_lake",
    )
    assert env["KAFKA_BOOTSTRAP_SERVERS"] == "broker:9093"
    assert env["REDIS_DB"] == "1"
    assert "REDIS_URL" not in env
    assert "REDIS_PASSWORD" not in env


def test_stage_argv_calls_cli_and_skips_stage4_by_default():
    mod = _stage_env()
    defaults = dict(mod.AIRFLOW_VARIABLES)
    assert mod.stage_argv("stage1", defaults) == ["cli.py", "scrapy", "--spiders", "scout"]
    deep = dict(defaults, pipeline_spider="deep_dive")
    assert mod.stage_argv("stage1", deep) == [
        "cli.py",
        "scrapy",
        "--spiders",
        "deep_dive",
        "--experimental",
    ]
    assert mod.stage_argv("stage2", defaults) == [
        "cli.py",
        "pipeline",
        "--skip-stage1",
        "--skip-stage3",
        "--stage2-workers",
        "100",
        "--stage2-batch",
        "200",
    ]
    stage3 = mod.stage_argv("stage3", defaults)
    assert stage3[:4] == ["cli.py", "pipeline", "--skip-stage1", "--skip-stage2"]
    assert "--stage3-workers" in stage3 and "50" in stage3
    assert "--stage3-batch" in stage3 and "100" in stage3
    assert mod.stage_argv("stage4", defaults) is None
    for flag in ("true", "yes", "on", "1", "TRUE"):
        argv = mod.stage_argv("stage4", dict(defaults, pipeline_run_stage4=flag))
        assert argv is not None and argv[0] == "-c"
        assert "PipelineOrchestrator().run_stage_by_name('stage4')" in argv[1]
        assert "src.workers" not in argv[1]
    assert mod.stage_argv("stage4", dict(defaults, pipeline_run_stage4="false")) is None
    try:
        mod.stage_argv("stage9", defaults)
    except ValueError as exc:
        assert "stage9" in str(exc)
    else:
        raise AssertionError("unknown stage must fail")
    try:
        mod.stage_argv("stage2", dict(defaults, pipeline_stage2_workers="0"))
    except ValueError as exc:
        assert "pipeline_stage2_workers" in str(exc)
    else:
        raise AssertionError("non-positive worker count must fail")


def test_dag_parses_and_is_not_part_of_core_compose():
    dag_src = (EXAMPLE / "scraping_pipeline_dag.py").read_text(encoding="utf-8")
    ast.parse(dag_src)
    assert "schedule=None" in dag_src
    assert "stage_env" in dag_src
    assert "cli.py" in dag_src or "stage_argv" in dag_src
    assert "src.workers" not in dag_src
    # The DAG may say REDIS_URL is not set. It must not assign one.
    assert "REDIS_URL=" not in dag_src
    assert "redis://" not in dag_src
    # The docstring may name docker-compose.yml as out of scope. It must not start Compose.
    assert "compose up" not in dag_src
    assert "docker compose" not in dag_src
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "airflow" not in compose.lower()
    assert "prefect" not in compose.lower()
    for rel in _LOCKFILES:
        text = (ROOT / rel).read_text(encoding="utf-8").lower()
        assert "airflow" not in text, rel
        assert "prefect" not in text, rel
