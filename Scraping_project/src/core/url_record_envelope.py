"""Stage 1 -> Stage 2 URL record envelope contract (#667).

Scout queues each HTML URL for Stage 2 as one envelope row
(``ScoutSpider._queue_for_stage2``); Stage 2 reads it back from ``stage2_queue``.
``envelope_version`` names the layout of the required fields. A consumer rejects a
version it does not implement instead of guessing, and ignores unknown extra fields
so producers can add optional data without a version bump.

Rows written before the envelope existed carry no ``envelope_version``. Stage 2
does not validate those (see ``stage2_worker``), so rolling deploys keep working.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

URL_RECORD_ENVELOPE_VERSION = 1

# Required fields: name -> type. Strings must also be non-blank.
REQUIRED_URL_RECORD_FIELDS: dict[str, type] = {
    "envelope_version": int,
    "url": str,
    "status": str,
    "queued_by": str,
    "crawl_job_id": str,
}

# Optional fields: name -> type. ``None`` is always accepted.
OPTIONAL_URL_RECORD_FIELDS: dict[str, type] = {
    "url_hash": str,
    "parent_url": str,
    "content_hint": str,
    "priority": int,
    "discovery_source": str,
    "discovery_method": str,
    "target_stage": str,
}


class UrlRecordEnvelopeError(ValueError):
    """A stage1 -> stage2 URL record does not satisfy the envelope contract."""


def validate_url_record_envelope(record: Mapping[str, Any]) -> None:
    """Raise ``UrlRecordEnvelopeError`` unless ``record`` satisfies the current contract.

    The version is checked first: a record from another version may not carry the
    current fields at all, so field errors would not be actionable. Unknown fields
    are ignored.
    """
    version = record.get("envelope_version")
    if type(version) is not int or version != URL_RECORD_ENVELOPE_VERSION:
        raise UrlRecordEnvelopeError(
            f"unsupported envelope_version {version!r}; expected {URL_RECORD_ENVELOPE_VERSION}"
        )

    problems: list[str] = []
    for name, expected in REQUIRED_URL_RECORD_FIELDS.items():
        value = record.get(name)
        if type(value) is not expected:
            problems.append(f"{name} must be {expected.__name__}, got {value!r}")
        elif expected is str and not value.strip():
            problems.append(f"{name} is blank")
    for name, expected in OPTIONAL_URL_RECORD_FIELDS.items():
        value = record.get(name)
        if value is not None and type(value) is not expected:
            problems.append(f"{name} must be {expected.__name__} or null, got {value!r}")

    if problems:
        raise UrlRecordEnvelopeError("; ".join(problems))
