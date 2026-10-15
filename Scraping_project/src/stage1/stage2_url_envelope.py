"""Stage 1 publisher for the versioned stage2 URL envelope (#667)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.core.stage2_url_envelope import (
    ENVELOPE_VERSION,
    OPTIONAL_FIELDS,
    REQUIRED_FIELDS,
    Stage2UrlEnvelopeError,
    validate_stage2_url_envelope,
)
from src.utils.url_canon import url_hash

_UNSET: Any = object()
_CONTRACT_FIELDS = frozenset(REQUIRED_FIELDS) | frozenset(OPTIONAL_FIELDS)


def publish_stage2_url_record(
    *,
    url: str,
    crawl_job_id: str,
    job_id: str,
    parent_url: Any = _UNSET,
    content_hint: Any = _UNSET,
    priority: Any = _UNSET,
    status: Any = _UNSET,
    queued_at: Any = _UNSET,
    queued_by: Any = _UNSET,
    discovery_source: Any = _UNSET,
    target_stage: Any = _UNSET,
    is_heavy: Any = _UNSET,
    depth: Any = _UNSET,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a version 1 envelope. ``url_hash`` is the pipeline canonical hash.

    Unknown keys in ``extra`` are kept. Keys that are already part of the
    contract must be passed as arguments, not via ``extra``.

    Raises:
        Stage2UrlEnvelopeError: the record would not satisfy the contract,
            including a wrong type or a blank required string.
    """
    if extra is not None and not isinstance(extra, Mapping):
        raise Stage2UrlEnvelopeError(f"stage1->stage2 URL envelope extra must be a mapping, got {type(extra).__name__}")
    if extra:
        if any(not isinstance(name, str) for name in extra):
            raise Stage2UrlEnvelopeError("stage1->stage2 URL envelope extra field names must be strings")
        conflicts = sorted(name for name in extra if name in _CONTRACT_FIELDS)
        if conflicts:
            raise Stage2UrlEnvelopeError(
                "stage1->stage2 URL envelope extra field(s) conflict with the contract: " + ", ".join(conflicts)
            )

    record: dict[str, Any] = {
        "envelope_version": ENVELOPE_VERSION,
        "url": url,
        "crawl_job_id": crawl_job_id,
        "job_id": job_id,
    }
    if isinstance(url, str):
        record["url_hash"] = url_hash(url)
    provided = {
        "parent_url": parent_url,
        "content_hint": content_hint,
        "priority": priority,
        "status": status,
        "queued_at": queued_at,
        "queued_by": queued_by,
        "discovery_source": discovery_source,
        "target_stage": target_stage,
        "is_heavy": is_heavy,
        "depth": depth,
    }
    for name, value in provided.items():
        if value is not _UNSET:
            record[name] = value
    if extra:
        record.update(extra)
    return validate_stage2_url_envelope(record)
