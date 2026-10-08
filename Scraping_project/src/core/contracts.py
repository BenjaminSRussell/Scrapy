"""Versioned hand-off contracts between pipeline stages (#623, #659, #667, #668, #686).

Each lake hand-off row carries ``schema_version``. Producers ``stamp`` rows;
consumers ``check`` / ``split_valid`` them **before** doing work:

* ``schema_version`` missing: a legacy row (written before versioning), read as v1.
* ``schema_version`` == the contract's version: validated against the fields below.
* any other version (newer producer, garbage): rejected with a clear
  :class:`ContractError` and left untouched, so an upgraded consumer can take it.
* unknown extra fields are allowed and passed through (forward compatibility).
* a required field that is missing, ``None`` or of the wrong type is rejected,
  naming the field.

Boundaries:

* ``stage1_stage2``  ``stage2_queue`` rows (Scout / SeedManager -> Stage 2)
* ``stage2_stage3``  ``stage2_page_analysis`` rows (Stage 2 -> Stage 3)
* ``stage2_stage4``  ``stage4_large_docs`` rows (Stage 2 -> Stage 4)
* ``stage4_chunk``   in-memory chunk records Stage 4 summarises (``make_chunks``;
  same shape as ``tests.factories.stage4_chunk`` plus ``chunk_id``/``total``)
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

VERSION_FIELD = "schema_version"

_STR = (str,)
_INT = (int,)
_NUM = (int, float)
_BOOL = (bool,)
_LIST = (list, tuple)
_TS = (str, datetime, date)  # Delta reads give datetimes, JSON gives ISO strings


class ContractError(ValueError):
    """A hand-off row does not satisfy its stage contract."""

    def __init__(self, contract: str, errors: list[str], row: dict[str, Any] | None = None):
        self.contract = contract
        self.errors = list(errors)
        ident = ""
        if row:
            ident = f" ({row.get('url_hash') or row.get('url') or row.get('chunk_id') or '?'})"
        super().__init__(f"{contract} contract violation{ident}: " + "; ".join(errors))


@dataclass(frozen=True)
class Contract:
    name: str
    version: int
    required: dict[str, tuple[type, ...]]
    optional: dict[str, tuple[type, ...]] = field(default_factory=dict)
    non_empty: tuple[str, ...] = ()

    def errors(self, row: Any) -> list[str]:
        if not isinstance(row, dict):
            return [f"expected a mapping, got {type(row).__name__}"]
        version = row.get(VERSION_FIELD)
        if version is not None:
            if isinstance(version, bool) or not isinstance(version, int):
                return [f"{VERSION_FIELD} must be an int, got {version!r}"]
            if version != self.version:
                newer = " (newer than this consumer; upgrade it)" if version > self.version else ""
                return [f"{VERSION_FIELD} {version} is not supported (expected {self.version}){newer}"]
        problems = []
        for name, types in self.required.items():
            value = row.get(name)
            if value is None:
                problems.append(f"missing required field {name!r}" if name not in row else f"{name!r} is null")
            elif not _is(value, types):
                problems.append(f"{name!r} must be {_names(types)}, got {type(value).__name__}")
            elif name in self.non_empty and not value:
                problems.append(f"{name!r} must not be empty")
        for name, types in self.optional.items():
            value = row.get(name)
            if value is not None and not _is(value, types):
                problems.append(f"{name!r} must be {_names(types)} when set, got {type(value).__name__}")
        return problems


def _is(value: Any, types: tuple[type, ...]) -> bool:
    if isinstance(value, bool) and bool not in types:
        return False  # bool is an int subclass; never accept it as a count
    return isinstance(value, types)


def _names(types: tuple[type, ...]) -> str:
    return "/".join(t.__name__ for t in types)


STAGE1_STAGE2 = Contract(
    "stage1_stage2",
    1,
    required={"url": _STR, "status": _STR},
    optional={
        "url_hash": _STR, "depth": _INT, "parent_url": _STR, "priority": _NUM, "retry_count": _INT,
        "crawl_job_id": _STR, "discovered_at": _TS, "queued_at": _TS, "content_hint": _STR,
    },
    non_empty=("url",),
)

STAGE2_STAGE3 = Contract(
    "stage2_stage3",
    1,
    required={"url": _STR, "url_hash": _STR, "text_content": _STR},
    optional={
        "title": _STR, "keywords": _LIST, "word_count": _INT, "quality_score": _NUM,
        "is_low_quality": _BOOL, "is_massive_doc": _BOOL, "has_error": _BOOL,
        "content_length": _INT, "processed_at": _TS,
    },
    non_empty=("url", "url_hash", "text_content"),
)

STAGE2_STAGE4 = Contract(
    "stage2_stage4",
    1,
    required={"url": _STR, "url_hash": _STR, "status": _STR},
    optional={
        "word_count": _INT, "content_length": _INT, "text_content": _STR,
        "is_pdf": _BOOL, "content_type": _STR, "queued_at": _TS,
    },
    non_empty=("url",),
)

STAGE4_CHUNK = Contract(
    "stage4_chunk",
    1,
    required={"url": _STR, "url_hash": _STR, "chunk_index": _INT, "start": _INT, "end": _INT, "text": _STR},
    optional={"chunk_id": _STR, "total": _INT},
    non_empty=("url_hash",),
)

CONTRACTS = {c.name: c for c in (STAGE1_STAGE2, STAGE2_STAGE3, STAGE2_STAGE4, STAGE4_CHUNK)}


def stamp(row: dict[str, Any], contract: Contract) -> dict[str, Any]:
    """Producer side: set ``schema_version`` (in place; returns ``row``)."""
    row.setdefault(VERSION_FIELD, contract.version)
    return row


def check(row: dict[str, Any], contract: Contract) -> dict[str, Any]:
    """Consumer side: return ``row`` or raise :class:`ContractError`."""
    problems = contract.errors(row)
    if problems:
        raise ContractError(contract.name, problems, row if isinstance(row, dict) else None)
    return row


def split_valid(
    rows: Iterable[dict[str, Any]], contract: Contract
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], ContractError]]]:
    """(valid rows, [(rejected row, error)]) without raising."""
    valid: list[dict[str, Any]] = []
    rejected: list[tuple[dict[str, Any], ContractError]] = []
    for row in rows:
        problems = contract.errors(row)
        if problems:
            rejected.append((row, ContractError(contract.name, problems, row if isinstance(row, dict) else None)))
        else:
            valid.append(row)
    return valid, rejected


def make_chunks(url: str, url_hash: str, text: str, spans: list[tuple[int, int]]) -> list[dict[str, Any]]:
    """Chunk records for ``text`` at ``spans`` (from ``chunk_spans``): stable ids, order, source refs."""
    total = len(spans)
    return [
        stamp(
            {
                "chunk_id": f"{url_hash}:{i:05d}",
                "url_hash": url_hash,
                "url": url,
                "chunk_index": i,
                "total": total,
                "start": start,
                "end": end,
                "text": text[start:end],
            },
            STAGE4_CHUNK,
        )
        for i, (start, end) in enumerate(spans)
    ]


try:  # rejected hand-off rows (#623)
    from prometheus_client import Counter

    CONTRACT_REJECTS = Counter(
        "handoff_contract_rejects_total",
        "Stage hand-off rows rejected by their versioned contract.",
        ["contract", "reason"],
    )
except Exception:  # prometheus_client missing or already registered
    CONTRACT_REJECTS = None


def record_rejects(rejected: list[tuple[dict[str, Any], ContractError]], log: Any) -> None:
    """Log and count rejects (reason: ``version`` or ``fields``)."""
    for _row, err in rejected:
        reason = "version" if any(VERSION_FIELD in e for e in err.errors) else "fields"
        log.warning(str(err))
        if CONTRACT_REJECTS is not None:
            CONTRACT_REJECTS.labels(contract=err.contract, reason=reason).inc()
