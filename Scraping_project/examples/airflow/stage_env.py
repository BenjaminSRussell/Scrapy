"""Map an Airflow connection onto the pipeline env contract, and build one-shot CLI argv.

The example DAG in this directory is the only caller. Compose does not import
this module, and nothing here imports Airflow or the pipeline package.

Redis is ``REDIS_HOST`` / ``REDIS_PORT`` / ``REDIS_PASSWORD`` / ``REDIS_DB``.
``REDIS_URL`` is removed from the child environment so a scheduler that has
one cannot override the connection (#511).
"""

from __future__ import annotations

import re

# Airflow Variable name -> default. Empty ``pipeline_project_dir`` means the
# task must fail until an operator sets it (no implicit checkout path).
AIRFLOW_VARIABLES: dict[str, str] = {
    "pipeline_project_dir": "",
    "pipeline_python": "python3",
    "pipeline_spider": "scout",
    "pipeline_stage2_workers": "100",
    "pipeline_stage2_batch": "200",
    "pipeline_stage3_workers": "50",
    "pipeline_stage3_batch": "100",
    "pipeline_run_stage4": "false",
    "delta_lake_path": "./data/delta_lake",
    "pipeline_log_level": "INFO",
}

# Airflow connection id -> required or optional. Optional connections are
# omitted from the environment when the id does not exist.
CONNECTIONS: dict[str, str] = {
    "scraping_redis": "required",
    "scraping_postgres": "optional",
    "scraping_kafka": "optional",
}

# cli.py pipeline has no stage 4 subcommand. This is one shot of
# PipelineOrchestrator.run_stage_by_name, not ``python -m src.workers.stage4_worker``
# (that module loops until SIGTERM and is the Compose entrypoint).
STAGE4_CODE = (
    "from src.orchestrator.pipeline_orchestrator import PipelineOrchestrator; "
    "PipelineOrchestrator().run_stage_by_name('stage4')"
)

_STAGES = ("stage1", "stage2", "stage3", "stage4")
_TRUE = {"1", "true", "yes", "on"}
_SPIDER = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
# Cleared when that optional connection is absent, so the child does not
# inherit the scheduler's database or broker.
_POSTGRES_KEYS = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
_INT_VARS = {
    "stage2": ("pipeline_stage2_workers", "pipeline_stage2_batch"),
    "stage3": ("pipeline_stage3_workers", "pipeline_stage3_batch"),
}


def _positive_int(name: str, raw: object) -> str:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Airflow Variable {name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"Airflow Variable {name} must be >= 1")
    return str(value)


def _spider(raw: object) -> str:
    name = str(raw or "").strip()
    if not _SPIDER.fullmatch(name):
        raise ValueError(
            "Airflow Variable pipeline_spider must be a spider name (letters, digits, _ or -). "
            "The supported production spider is scout."
        )
    return name


def stage4_enabled(variables: dict[str, str]) -> bool:
    """True only for the documented truthy values. Anything else, including empty, is off."""
    return str(variables.get("pipeline_run_stage4", "false")).strip().lower() in _TRUE


def stage_argv(stage: str, variables: dict[str, str]) -> list[str] | None:
    """Argv after the interpreter, run with cwd = ``pipeline_project_dir``.

    ``None`` means the task is a no-op (stage 4 stays off unless
    ``pipeline_run_stage4`` is true). Stages 2 and 3 use ``cli.py pipeline``
    skip flags so each task runs one stage and exits. Stage 1 uses
    ``cli.py scrapy`` so ``pipeline_spider`` is honored.
    """
    if stage not in _STAGES:
        raise ValueError(f"Unknown stage: {stage}")
    if stage == "stage4":
        if not stage4_enabled(variables):
            return None
        return ["-c", STAGE4_CODE]
    if stage == "stage1":
        spider = _spider(variables.get("pipeline_spider", "scout"))
        argv = ["cli.py", "scrapy", "--spiders", spider]
        # cli.py refuses experimental spiders unless this flag is set.
        if spider != "scout":
            argv.append("--experimental")
        return argv
    workers, batch = _INT_VARS[stage]
    skip = "--skip-stage3" if stage == "stage2" else "--skip-stage2"
    flag_workers = "--stage2-workers" if stage == "stage2" else "--stage3-workers"
    flag_batch = "--stage2-batch" if stage == "stage2" else "--stage3-batch"
    return [
        "cli.py",
        "pipeline",
        "--skip-stage1",
        skip,
        flag_workers,
        _positive_int(workers, variables.get(workers, AIRFLOW_VARIABLES[workers])),
        flag_batch,
        _positive_int(batch, variables.get(batch, AIRFLOW_VARIABLES[batch])),
    ]


def _host_port(spec: dict, default_port: int) -> tuple[str, str]:
    host = str(spec.get("host") or "").strip()
    raw_port = spec.get("port")
    port = default_port if raw_port in (None, "") else int(raw_port)
    return host, str(port)


def _redis_db(extra: object) -> str:
    if not isinstance(extra, dict):
        extra = {}
    raw = extra.get("db", None)
    if raw is None or raw == "":
        raw = extra.get("database", 0)
    if raw is None or raw == "":
        raw = 0
    return str(int(raw))


def stage_env(
    *,
    base: dict[str, str],
    redis: dict,
    postgres: dict | None = None,
    kafka: dict | None = None,
    delta_lake_path: str,
    log_level: str = "INFO",
    worker_id: str | None = None,
) -> dict[str, str]:
    """Child environment for one stage process.

    ``redis`` / ``postgres`` / ``kafka`` are plain dicts (host, port, password,
    schema, login, extra), not Airflow connection objects, so this stays
    importable without Airflow. ``scraping_redis.host`` is required.
    """
    lake = str(delta_lake_path or "").strip()
    if not lake:
        raise ValueError("Airflow Variable delta_lake_path is empty")
    host, port = _host_port(redis, 6379)
    if not host:
        raise ValueError("Airflow connection scraping_redis needs a host (REDIS_HOST)")

    env = {str(key): str(value) for key, value in base.items()}
    env.pop("REDIS_URL", None)
    env["REDIS_HOST"] = host
    env["REDIS_PORT"] = port
    password = redis.get("password")
    if password:
        env["REDIS_PASSWORD"] = str(password)
    else:
        env.pop("REDIS_PASSWORD", None)
    env["REDIS_DB"] = _redis_db(redis.get("extra"))
    env["DELTA_LAKE_PATH"] = lake
    env["LOG_LEVEL"] = str(log_level or "INFO").strip() or "INFO"
    if worker_id:
        env["WORKER_ID"] = worker_id

    if postgres is None:
        for key in _POSTGRES_KEYS:
            env.pop(key, None)
    else:
        pg_host, pg_port = _host_port(postgres, 5432)
        if not pg_host:
            raise ValueError("Airflow connection scraping_postgres needs a host (DB_HOST)")
        env["DB_HOST"] = pg_host
        env["DB_PORT"] = pg_port
        env["DB_NAME"] = str(postgres.get("schema") or "scraping_pipeline")
        env["DB_USER"] = str(postgres.get("login") or "scrapy_app")
        pg_password = postgres.get("password")
        if pg_password:
            env["DB_PASSWORD"] = str(pg_password)
        else:
            env.pop("DB_PASSWORD", None)

    bootstrap = ""
    if kafka is not None:
        bootstrap = str(kafka.get("host") or "").strip()
        if bootstrap and ":" not in bootstrap:
            _, kafka_port = _host_port(kafka, 9092)
            bootstrap = f"{bootstrap}:{kafka_port}"
    if bootstrap:
        env["KAFKA_BOOTSTRAP_SERVERS"] = bootstrap
    else:
        env.pop("KAFKA_BOOTSTRAP_SERVERS", None)

    env.pop("REDIS_URL", None)
    return env
