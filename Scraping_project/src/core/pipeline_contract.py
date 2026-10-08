"""Authoritative pipeline hop table and config drift check (#948).

Every hop between stages is a Delta table. Table names are owned by code
(``src/core/constants.py``): schemas (``src/core/schemas.py``), partitioning
and Z-order are keyed by those names, so renaming a table through config
would silently drop enforcement. ``config.yml`` ``delta_lake.tables`` mirrors
the names for operators and is *validated* against this table at worker
startup; it is not a rename mechanism.

There are no Redis hop queues. The old ``message_queues`` section named
queues no code ever read or wrote, so "fixing" it changed nothing; its
presence is now reported.

``tests/unit/test_pipeline_contract.py`` scans the processor modules and
fails when this table and the code disagree.
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
        writes=(C.TABLE_STAGE2_QUEUE, C.TABLE_JS_SPIDER_QUEUE, C.TABLE_STAGE1_OFFSITE_CANDIDATES, C.TABLE_METADATA_QUEUE),
        note="Scout spider items -> QueueItemPipeline hands URLs to Stage 2 / the JS renderer",
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
        note="rendered pages flow back through the item pipelines; closed() rewrites queue status",
    ),
    Hop(
        "stage2",
        ("src.stage2.stage2_worker", "src.stage2.intelligent_analyzer"),
        reads=(C.TABLE_STAGE2_QUEUE, C.TABLE_STAGE2_ERRORS),
        writes=(C.TABLE_STAGE2_PAGE_ANALYSIS, C.TABLE_STAGE2_ERRORS, C.TABLE_STAGE4_LARGE_DOCS),
        note="queue status is MERGEd back into stage2_queue",
    ),
    Hop(
        "stage3",
        ("src.stage3.stage3_worker",),
        reads=(C.TABLE_STAGE2_PAGE_ANALYSIS, C.TABLE_STAGE3_SUMMARIES, C.LEGACY_TABLE_STAGE3_SUMMARIES),
        writes=(C.TABLE_STAGE3_SUMMARIES,),
        note="stage4_summaries is read only, so pre-#612 lakes are not re-summarized",
    ),
    Hop(
        "stage4",
        ("src.stage4.stage4_worker",),
        reads=(C.TABLE_STAGE4_LARGE_DOCS, C.TABLE_STAGE2_PAGE_ANALYSIS, C.TABLE_STAGE4_LARGE_DOC_SUMMARIES),
        writes=(C.TABLE_STAGE4_LARGE_DOC_SUMMARIES, C.TABLE_STAGE4_LARGE_DOCS),
        note="stage4_large_docs status is a row-level MERGE, never an overwrite",
    ),
)

#: Tables only kept for backward-compatible reads; nothing may write them.
READ_ONLY_LEGACY = frozenset({C.LEGACY_TABLE_STAGE3_SUMMARIES})

#: Every table name the pipeline reads or writes.
KNOWN_TABLES = frozenset(
    {C.TABLE_UCONN_URLS}
    | {t for hop in HOPS for t in hop.reads + hop.writes}
)


def contract_drift(config: Any) -> list[str]:
    """Problems between ``config.yml`` and the hop table (empty == aligned)."""
    problems: list[str] = []
    tables = config.get("delta_lake.tables") or {}
    if not isinstance(tables, dict):
        problems.append("config.yml delta_lake.tables must be a mapping of table key -> name")
        tables = {}
    for key, value in tables.items():
        if key not in KNOWN_TABLES:
            problems.append(
                f"config.yml delta_lake.tables.{key} names a table no stage reads or writes; remove it"
            )
            continue
        if value != key:
            problems.append(
                f"config.yml delta_lake.tables.{key}={value!r} is ignored: table names are fixed in "
                f"src/core/constants.py (schemas and partitioning are keyed by them); the pipeline uses {key!r}"
            )
        if key in READ_ONLY_LEGACY:
            problems.append(
                f"config.yml delta_lake.tables.{key} is a legacy read-only table (Stage 3 output before #612); "
                f"Stage 4 writes {C.TABLE_STAGE4_LARGE_DOC_SUMMARIES}"
            )
    if config.get("message_queues") is not None:
        problems.append(
            "config.yml message_queues is not used: no worker reads or writes those Redis queues "
            "(every hop is a Delta table, see src/core/pipeline_contract.HOPS); remove the section"
        )
    return problems


_logged = False
_logged_lock = threading.Lock()


def log_contract_drift(config: Optional[Any] = None) -> list[str]:
    """Log drift once per process (worker/orchestrator startup)."""
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
    return problems


def hop_table_markdown() -> str:
    """The hop table as Markdown (README / docs)."""
    lines = ["| Stage | Module(s) | Reads | Writes | Note |", "|---|---|---|---|---|"]
    for hop in HOPS:
        def fmt(names: tuple[str, ...]) -> str:
            return ", ".join(f"`{n}`" for n in names) or "-"
        lines.append(
            f"| {hop.stage} | {', '.join(f'`{m}`' for m in hop.modules)} | {fmt(hop.reads)} | {fmt(hop.writes)} | {hop.note} |"
        )
    return "\n".join(lines)
