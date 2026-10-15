"""Authoritative pipeline hop table and config drift check (#948).

Each hop is input table → processor module → output table. Table names are
owned by ``src/core/constants.py`` (schemas and partitioning are keyed by
those strings). ``config.yml`` ``delta_lake.tables`` mirrors them for
operators. Stage 3 is the exception: it appends to
``delta_lake.tables.stage3_summaries`` (#316).

There is no Redis hop queue. The old ``message_queues`` section named lists
no worker reads or writes. Stage handoffs are Delta tables. The live Redis
frontier is ``js_spider:priority_queue``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Optional

from src.core import constants as C

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Hop:
    stage: str
    modules: tuple[str, ...]
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    note: str = ""


HOPS: tuple[Hop, ...] = (
    Hop(
        "stage1",
        ("src.pipelines",),
        reads=(),
        writes=(
            C.TABLE_STAGE2_QUEUE,
            C.TABLE_JS_SPIDER_QUEUE,
            C.TABLE_STAGE1_OFFSITE_CANDIDATES,
            C.TABLE_METADATA_QUEUE,
        ),
        note="Scout items via QueueItemPipeline, offsite candidates, and metadata",
    ),
    Hop(
        "stage1-experimental",
        ("src.stage1.experimental.base_spider",),
        reads=(C.TABLE_SEED_URLS,),
        writes=(C.TABLE_STAGE1_DISCOVERY, C.TABLE_STAGE1_ERRORS),
    ),
    Hop(
        "stage1-js",
        ("src.stage1.experimental.js_spider",),
        reads=(C.TABLE_JS_SPIDER_QUEUE,),
        writes=(C.TABLE_JS_SPIDER_QUEUE,),
        note="closed() rewrites queue status",
    ),
    Hop(
        "stage2",
        ("src.stage2.stage2_worker", "src.stage2.intelligent_analyzer"),
        reads=(C.TABLE_STAGE2_QUEUE, C.TABLE_STAGE2_ERRORS),
        writes=(
            C.TABLE_STAGE2_PAGE_ANALYSIS,
            C.TABLE_STAGE2_ERRORS,
            C.TABLE_STAGE4_LARGE_DOCS,
            C.TABLE_STAGE2_QUEUE,
        ),
        note="analysis and queue status are row-level MERGEs",
    ),
    Hop(
        "stage3",
        ("src.stage3.stage3_worker",),
        reads=(C.TABLE_STAGE2_PAGE_ANALYSIS, C.TABLE_STAGE3_SUMMARIES, C.LEGACY_TABLE_STAGE3_SUMMARIES),
        writes=(C.TABLE_STAGE3_SUMMARIES,),
        note="append target is config delta_lake.tables.stage3_summaries; stage4_summaries is read-only",
    ),
    Hop(
        "stage4",
        ("src.stage4.stage4_worker",),
        reads=(C.TABLE_STAGE4_LARGE_DOCS, C.TABLE_STAGE2_PAGE_ANALYSIS, C.TABLE_STAGE4_LARGE_DOC_SUMMARIES),
        writes=(C.TABLE_STAGE4_LARGE_DOC_SUMMARIES, C.TABLE_STAGE4_LARGE_DOCS),
        note="queue status is a row-level MERGE, never an overwrite",
    ),
    Hop(
        "stage4-legacy",
        ("src.stage4.large_doc_processor",),
        reads=(C.TABLE_STAGE4_LARGE_DOCS,),
        writes=(C.TABLE_STAGE4_LARGE_DOC_SUMMARIES, C.TABLE_STAGE4_LARGE_DOCS),
        note="process_queue entry point; the orchestrator uses stage4_worker",
    ),
)

# Read by Stage 3 so pre-#612 lakes are not summarized again. Not a write target.
READ_ONLY_LEGACY = frozenset({C.LEGACY_TABLE_STAGE3_SUMMARIES})

# Real tables that are not a stage hop (still valid config.yml names).
SIDE_TABLES = frozenset({C.TABLE_UCONN_URLS, C.TABLE_STAGE3_ANALYTICS, C.TABLE_ENTITY_SUMMARIES})

USED_TABLES = frozenset(t for hop in HOPS for t in hop.reads + hop.writes) | SIDE_TABLES


def _get(config: Any, key: str, default: Any = None) -> Any:
    """Dotted lookup for a Config object or a raw config.yml mapping."""
    if isinstance(config, dict):
        if key in config:
            return config[key]
        cur: Any = config
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur
    getter = getattr(config, "get", None)
    if callable(getter):
        return getter(key, default)
    return default


def stage3_summary_table(config: Any | None = None) -> str:
    """Table Stage 3 appends to: ``delta_lake.tables.stage3_summaries`` (#316, #948).

    Missing or blank config falls back to ``TABLE_STAGE3_SUMMARIES``.
    """
    if config is None:
        try:
            from src.core.config import get_config

            config = get_config()
        except Exception:
            return C.TABLE_STAGE3_SUMMARIES
    tables = _get(config, "delta_lake.tables", None)
    if isinstance(tables, dict):
        name = tables.get(C.TABLE_STAGE3_SUMMARIES)
        if isinstance(name, str) and name.strip():
            return name.strip()
    return C.TABLE_STAGE3_SUMMARIES


def _declared(config: Any) -> set[str]:
    names: set[str] = set()
    tables = _get(config, "delta_lake.tables", None) or {}
    if isinstance(tables, dict):
        names.update(str(key) for key in tables)
        names.update(value for value in tables.values() if isinstance(value, str))
    extra = _get(config, "delta_lake.extra_tables", None) or []
    if isinstance(extra, (list, tuple)):
        names.update(str(item) for item in extra)
    return names


def contract_drift(config: Any) -> list[str]:
    """Problems between config.yml and the hop table (empty means aligned)."""
    problems: list[str] = []
    tables = _get(config, "delta_lake.tables", None)
    if tables is None:
        tables = {}
    elif not isinstance(tables, dict):
        problems.append("config.yml delta_lake.tables must be a mapping of table key -> name")
        tables = {}
    for key, value in tables.items():
        if key not in USED_TABLES:
            problems.append(
                f"config.yml delta_lake.tables.{key} names a table no stage reads or writes; remove it"
            )
            continue
        if not isinstance(value, str) or not value.strip():
            problems.append(f"config.yml delta_lake.tables.{key} must be a non-empty table name")
            continue
        if key == C.TABLE_STAGE3_SUMMARIES and value != C.TABLE_STAGE3_SUMMARIES:
            problems.append(
                f"config.yml delta_lake.tables.stage3_summaries={value!r} is what Stage 3 writes; "
                f"schemas and partitions stay keyed by {C.TABLE_STAGE3_SUMMARIES!r}"
            )
        elif value != key:
            problems.append(
                f"config.yml delta_lake.tables.{key}={value!r} is ignored: workers use {key!r} "
                f"(src/core/constants.py). Stage 3 is the only table read from this map"
            )
    declared = _declared(config)
    for table in sorted({t for hop in HOPS for t in hop.writes}):
        if table not in declared:
            problems.append(
                f"config.yml delta_lake.tables/extra_tables does not declare {table}, which a stage writes"
            )
    if _get(config, "message_queues", None) is not None:
        problems.append(
            "config.yml message_queues is not used: no worker reads or writes those Redis lists "
            "(every hop is a Delta table; see src/core/pipeline_contract.HOPS). Remove the section"
        )
    return problems


_logged = False
_logged_lock = threading.Lock()


def log_contract_drift(config: Optional[Any] = None) -> list[str]:
    """Log drift once per process (CLI, orchestrator, and stage-worker startup)."""
    global _logged
    with _logged_lock:
        if _logged:
            return []
        _logged = True
    if config is None:
        from src.core.config import get_config

        config = get_config()
    problems = contract_drift(config)
    for problem in problems:
        logger.warning("[contract] %s", problem)
    if not problems:
        logger.info("[contract] delta_lake.tables matches the hop table")
    return problems


def hop_table_markdown() -> str:
    """The hop table as Markdown (docs/STAGE_MAP.md)."""
    lines = ["| Stage | Module(s) | Reads | Writes | Note |", "|---|---|---|---|---|"]
    for hop in HOPS:
        reads = ", ".join(f"`{name}`" for name in hop.reads) or "-"
        writes = ", ".join(f"`{name}`" for name in hop.writes) or "-"
        modules = ", ".join(f"`{name}`" for name in hop.modules)
        note = f" {hop.note} " if hop.note else " "
        lines.append(f"| {hop.stage} | {modules} | {reads} | {writes} |{note}|")
    return "\n".join(lines)
