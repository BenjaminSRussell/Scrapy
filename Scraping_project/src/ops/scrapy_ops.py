"""``scrapy-ops lake compact|vacuum|doctor`` (#1098).

One entrypoint for Delta compaction, vacuum, and the operator health check.
``doctor`` pings Redis with ``REDIS_HOST`` / ``REDIS_PORT`` / ``REDIS_PASSWORD`` /
``REDIS_DB``, lists the Kafka topics ``config.yml`` declares, and fails when a
table has data but no ``_delta_log``.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Sequence, cast

# Same names LakehouseManager registers. A unit test fails if they drift.
LAKE_TABLES: tuple[str, ...] = (
    "seed_urls",
    "uconn_urls",
    "stage1_discovery",
    "stage1_errors",
    "stage1_offsite_candidates",
    "js_spider_queue",
    "stage2_queue",
    "stage2_page_analysis",
    "stage2_errors",
    "stage3_analytics",
    "stage3_summaries",
    "stage4_large_docs",
    "stage4_large_doc_summaries",
    "stage4_summaries",
    "metadata_queue",
)

_TABLE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def lake_base(explicit: str | None) -> Path:
    """Lake directory: ``--base``, else ``DELTA_LAKE_PATH``, else config."""
    if explicit:
        return Path(explicit)
    env = os.getenv("DELTA_LAKE_PATH")
    if env:
        return Path(env)
    from src.core.config import get_config

    return Path(get_config().get("delta_lake.base_path", "./data/delta_lake"))


def _table_names(requested: Sequence[str] | None, default: Sequence[str]) -> list[str]:
    if not requested:
        return list(default)
    bad = [name for name in requested if _TABLE_NAME.fullmatch(name) is None]
    if bad:
        raise ValueError(f"refusing table name(s): {', '.join(bad)}")
    return list(requested)


def _log_has_files(path: Path) -> bool:
    log = path / "_delta_log"
    if not log.is_dir():
        return False
    return any(entry.is_file() for entry in log.rglob("*"))


def _has_data_files(path: Path) -> bool:
    if not path.is_dir():
        return False
    for entry in path.rglob("*"):
        if entry.is_file() and "_delta_log" not in entry.parts:
            return True
    return False


def table_log_problem(path: Path, name: str) -> str | None:
    """``None`` when the table is healthy or not written yet.

    A directory with data files, or an empty ``_delta_log``, is a broken table.
    An empty directory (created before the first commit) is not a failure.
    """
    if _log_has_files(path):
        return None
    log = path / "_delta_log"
    if _has_data_files(path) or log.exists():
        return f"{name}: missing _delta_log"
    return None


def _open_health(path: Path, name: str) -> str | None:
    if not _log_has_files(path):
        return None
    try:
        from deltalake import DeltaTable

        DeltaTable(str(path))
    except Exception as exc:
        return f"{name}: Delta table unhealthy: {exc.__class__.__name__}"
    return None


def _is_single_table(base: Path) -> bool:
    if (base / "_delta_log").exists() or any(base.glob("*.parquet")):
        return not any((base / name).is_dir() for name in LAKE_TABLES)
    return False


def check_delta(base: Path, table_names: Sequence[str] | None = None) -> tuple[list[str], bool]:
    """Delta health lines. Does not create directories."""
    base = Path(base)
    if not base.exists():
        return [f"delta: FAIL missing Delta lake path: {base}"], False
    if not base.is_dir():
        return [f"delta: FAIL Delta path is not a directory: {base}"], False

    if _is_single_table(base):
        problem = table_log_problem(base, base.name) or _open_health(base, base.name)
        if problem:
            return [f"delta: FAIL {problem}"], False
        return [f"delta: ok {base.name}"], True

    names = _table_names(table_names, LAKE_TABLES)
    if table_names is None:
        extra = [
            child.name
            for child in sorted(base.iterdir())
            if child.is_dir() and child.name not in names and not child.name.startswith(".")
        ]
        names = names + extra

    problems: list[str] = []
    healthy = 0
    pending = 0
    for name in names:
        path = base / name
        if not path.exists():
            continue
        problem = table_log_problem(path, name)
        if problem:
            problems.append(f"delta: FAIL {problem}")
            continue
        if _log_has_files(path):
            health = _open_health(path, name)
            if health:
                problems.append(f"delta: FAIL {health}")
                continue
            healthy += 1
        else:
            pending += 1
    if problems:
        return problems, False
    return [f"delta: ok ({healthy} tables with _delta_log, {pending} not yet written)"], True


def ping_redis(timeout: float = 3.0) -> tuple[bool, str]:
    """PING using ``REDIS_HOST`` / ``PORT`` / ``PASSWORD`` / ``DB`` (#511)."""
    import redis

    from src.utils.redis_env import redis_settings

    settings = redis_settings()
    where = f"{settings.host}:{settings.port} db={settings.db}"
    try:
        client = redis.Redis(
            host=settings.host,
            port=settings.port,
            password=settings.password,
            db=settings.db,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
        )
        try:
            if client.ping() is not True:
                return False, f"redis: FAIL ping {where}: unexpected reply"
        finally:
            client.close()
    except Exception as exc:
        detail = str(exc)
        if settings.password and settings.password in detail:
            detail = detail.replace(settings.password, "***")
        return False, f"redis: FAIL ping {where}: {exc.__class__.__name__}: {detail}"
    return True, f"redis: ok {where}"


def check_kafka(timeout: float = 5.0) -> tuple[bool, str]:
    """Fail when a topic declared in config is absent. Read-only."""
    try:
        from confluent_kafka.admin import AdminClient

        from src.core.config import get_config
        from src.utils.kafka_topics import admin_config_from_env, topic_plan

        expected = [spec.name for spec in topic_plan(get_config())]
        # AdminClient stubs want a wider value type than dict[str, str], and dict is invariant.
        client_conf = cast(dict[str, str | int | float | bool], admin_config_from_env())
        meta = AdminClient(client_conf).list_topics(timeout=timeout)
        have = set(meta.topics)
    except Exception as exc:
        return False, f"kafka: FAIL {exc.__class__.__name__}: {exc}"
    missing = [name for name in expected if name not in have]
    if missing:
        return False, "kafka: FAIL missing topics: " + ", ".join(missing)
    return True, "kafka: ok " + ", ".join(expected)


def _manager(base: Path):
    from src.lakehouse.lakehouse_manager import LakehouseManager

    return LakehouseManager(base_path=str(base), start_workers=False)


def _require_lake(base: Path) -> str | None:
    if not base.exists():
        return f"delta: FAIL missing Delta lake path: {base}"
    if not base.is_dir():
        return f"delta: FAIL Delta path is not a directory: {base}"
    return None


def cmd_compact(base: Path, tables: Sequence[str] | None) -> int:
    problem = _require_lake(base)
    if problem:
        print(problem)
        return 1
    manager = _manager(base)
    try:
        names = _table_names(tables, tuple(manager.tables))
    except ValueError as exc:
        print(f"delta: FAIL {exc}")
        return 2
    code = 0
    for name in names:
        path = Path(manager.tables.get(name, base / name))
        broken = table_log_problem(path, name)
        if broken:
            print(f"delta: FAIL {broken}")
            code = 1
            continue
        if not _log_has_files(path):
            print(f"{name}: skipped (no Delta table yet)")
            continue
        manager.get_table_path(name)
        manager._optimize_table(name)
        print(f"{name}: compacted")
    return code


def cmd_vacuum(
    base: Path,
    tables: Sequence[str] | None,
    *,
    apply: bool,
    retention_hours: int | None,
    verbose: bool,
) -> int:
    problem = _require_lake(base)
    if problem:
        print(problem)
        return 1
    manager = _manager(base)
    try:
        names = _table_names(tables, tuple(manager.tables))
    except ValueError as exc:
        print(f"delta: FAIL {exc}")
        return 2
    total_files = 0
    total_bytes = 0
    code = 0
    for name in names:
        path = Path(manager.tables.get(name, base / name))
        broken = table_log_problem(path, name)
        if broken:
            print(f"delta: FAIL {broken}")
            code = 1
            continue
        if not _log_has_files(path):
            print(f"{name}: skipped (no Delta table yet)")
            continue
        hours = retention_hours if retention_hours is not None else manager.retention_hours_for(name)
        preview = manager.vacuum_preview(name, hours)
        files = list(preview["files"])
        total_files += len(files)
        total_bytes += int(preview["bytes"])
        print(f"{name}: {len(files)} files, {preview['bytes']} bytes older than {hours}h")
        if verbose:
            for filename in files:
                print(f"    {filename}")
        if apply and files:
            manager._vacuum_table(name, hours, enforce_retention_duration=hours >= 168)
    verb = "Reclaimed" if apply else "Would reclaim (preview; pass --apply to delete)"
    print(f"{verb}: {total_files} files, {total_bytes} bytes")
    return code


def cmd_doctor(base: Path, tables: Sequence[str] | None, *, redis_timeout: float, kafka_timeout: float) -> int:
    try:
        names: Sequence[str] | None
        if tables:
            names = _table_names(tables, LAKE_TABLES)
        else:
            names = None
    except ValueError as exc:
        print(f"delta: FAIL {exc}")
        return 2
    ok_redis, redis_line = ping_redis(redis_timeout)
    ok_kafka, kafka_line = check_kafka(kafka_timeout)
    delta_lines, ok_delta = check_delta(base, names)
    for line in (redis_line, kafka_line, *delta_lines):
        print(line)
    return 0 if ok_redis and ok_kafka and ok_delta else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scrapy-ops",
        description="Lake operations: compact, vacuum, and doctor (#1098).",
    )
    sub = parser.add_subparsers(dest="group", required=True)
    lake = sub.add_parser("lake", help="Delta lake compact, vacuum, and doctor")
    actions = lake.add_subparsers(dest="action", required=True)

    compact = actions.add_parser("compact", help="Compact Delta tables that have a _delta_log")
    compact.add_argument("--base", default=None, help="Lake directory (default DELTA_LAKE_PATH or config)")
    compact.add_argument("--table", action="append", default=None, help="Only this table (repeatable)")

    vacuum = actions.add_parser("vacuum", help="Preview Delta vacuum; --apply deletes")
    vacuum.add_argument("--base", default=None, help="Lake directory (default DELTA_LAKE_PATH or config)")
    vacuum.add_argument("--table", action="append", default=None, help="Only this table (repeatable)")
    vacuum.add_argument("--apply", action="store_true", help="Delete files. Default is a preview")
    vacuum.add_argument("--retention-hours", type=int, default=None, help="Override the retention tier")
    vacuum.add_argument("--verbose", action="store_true", help="List candidate files")

    doctor = actions.add_parser("doctor", help="Redis ping, Kafka topics, and Delta/_delta_log")
    doctor.add_argument("--base", default=None, help="Lake directory (default DELTA_LAKE_PATH or config)")
    doctor.add_argument("--table", action="append", default=None, help="Only this table (repeatable)")
    doctor.add_argument("--redis-timeout", type=float, default=3.0)
    doctor.add_argument("--kafka-timeout", type=float, default=5.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 2
    base = lake_base(args.base)
    if args.action == "compact":
        return cmd_compact(base, args.table)
    if args.action == "vacuum":
        return cmd_vacuum(
            base,
            args.table,
            apply=args.apply,
            retention_hours=args.retention_hours,
            verbose=args.verbose,
        )
    if args.action == "doctor":
        return cmd_doctor(
            base,
            args.table,
            redis_timeout=args.redis_timeout,
            kafka_timeout=args.kafka_timeout,
        )
    parser.error(f"unknown lake action: {args.action}")


def cli() -> None:
    """Console-script entrypoint (``scrapy-ops``)."""
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())
