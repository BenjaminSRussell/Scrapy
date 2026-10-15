"""Pipeline stages as the Control Center shows them, plus small shared helpers.

The Delta tables on disk do not match ``src/core/schemas.py`` (for example
``stage1_discovery`` has ``domain``/``depth``/``status_code`` but no ``parent_url``),
so each stage lists the columns it would like; readers use whichever exist.
Every table has ``_ingestion_time``, which is the event time everywhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

TS_COLUMN = "_ingestion_time"

# Freshness of a stage, from the age of its newest write (seconds).
ACTIVE_UNDER = 120
IDLE_UNDER = 1800


@dataclass(frozen=True)
class Stage:
    id: str
    title: str
    table: str
    event_kind: str
    columns: tuple[str, ...]


STAGES: tuple[Stage, ...] = (
    Stage("seeds", "Seeds", "seed_urls", "seeded",
          ("url", "domain", "priority", "seed_priority", TS_COLUMN)),
    Stage("discovery", "Discovery", "stage1_discovery", "discovered",
          ("url", "domain", "depth", "status_code", "content_size", "requires_js", "is_heavy",
           "urls_found", "parent_url", TS_COLUMN)),
    Stage("analysis", "Analysis", "stage2_page_analysis", "analyzed",
          ("url", "domain", "title", "word_count", "is_low_quality", "has_quality_content",
           "is_massive_doc", "quality_score", TS_COLUMN)),
    Stage("summaries", "Summaries", "stage3_summaries", "summarized",
          ("url", "title", "model", "summary", TS_COLUMN)),
    Stage("large_docs", "Large Docs", "stage4_large_doc_summaries", "large_doc",
          ("url", "title", "original_word_count", "summary_word_count", "compression_ratio",
           "summary", TS_COLUMN)),
)
STAGE_BY_ID: dict[str, Stage] = {s.id: s for s in STAGES}
EDGES: tuple[tuple[str, str], ...] = (
    ("seeds", "discovery"),
    ("discovery", "analysis"),
    ("analysis", "summaries"),
    ("analysis", "large_docs"),
)
ERRORS_TABLE = "stage1_errors"
ERROR_COLUMNS = ("url", "error_type", "error_message", "http_status_code", TS_COLUMN)

# Small per-kind payload carried by a live event (the rest is one fetch away).
_EVENT_FIELDS = {
    "seeds": (),
    "discovery": ("depth", "status_code"),
    "analysis": ("word_count",),
    "summaries": ("model",),
    "large_docs": ("compression_ratio",),
}


@dataclass
class StageState:
    status: str = "missing"  # ok | missing | error
    count: int | None = None
    last_write: float | None = None
    version: int | None = None
    error: str | None = None
    errors: int | None = None


@dataclass
class PollResult:
    stages: dict[str, StageState]
    events: list[dict] = field(default_factory=list)


def parse_ts(value) -> float | None:
    """Epoch seconds from an ISO string or datetime; naive values are UTC."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def freshness(last_write: float | None, now: float) -> str:
    if last_write is None:
        return "none"
    age = now - last_write
    if age < ACTIVE_UNDER:
        return "active"
    if age < IDLE_UNDER:
        return "idle"
    return "stale"


def is_low_quality(row: dict) -> bool | None:
    """The current stage-2 writer sets ``is_low_quality``; older tables have ``has_quality_content``."""
    if row.get("is_low_quality") is not None:
        return bool(row["is_low_quality"])
    if row.get("has_quality_content") is not None:
        return not row["has_quality_content"]
    return None


def routed_to(row: dict) -> str | None:
    """Where stage 2 sends a page: stage 3 takes quality non-massive pages, stage 4 quality
    massive ones; low-quality pages go nowhere."""
    massive = row.get("is_massive_doc")
    if massive is None or is_low_quality(row):
        return None
    return "large_docs" if massive else "summaries"


def event_fields(stage_id: str, row: dict) -> dict:
    out = {k: row[k] for k in _EVENT_FIELDS.get(stage_id, ()) if row.get(k) is not None}
    if stage_id == "analysis":
        out["routed"] = routed_to(row)
    return out


def make_event(stage: Stage, row: dict, seq: int, t: float) -> dict:
    return {"id": f"{stage.id}:{seq}", "stage": stage.id, "kind": stage.event_kind,
            "url": row.get("url") or "", "t": t, "fields": event_fields(stage.id, row)}


def make_error_event(row: dict, seq: int, t: float) -> dict:
    return {"id": f"error:{seq}", "stage": "discovery", "kind": "error", "url": row.get("url") or "",
            "t": t, "fields": {"error_type": row.get("error_type") or "unknown"}}
