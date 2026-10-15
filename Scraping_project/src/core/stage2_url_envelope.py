"""Versioned stage1 -> stage2 URL record envelope (#667).

Current version is ``ENVELOPE_VERSION`` (1).

Required fields: ``envelope_version``, ``url``, ``url_hash``, ``crawl_job_id``,
``job_id``. ``url_hash`` is 16-64 lowercase hex characters (the pipeline hash
is 16). ``crawl_job_id`` and ``job_id`` are the crawl identifiers.

Known optional fields are type-checked when present and may be omitted.
``parent_url`` may be null. Any other key is an unknown optional field: it is
kept, not type-checked, so a newer producer does not drop crawl metadata.

A record with no ``envelope_version`` key is a pre-contract queue row and is
not validated. Any other version raises ``Stage2UrlEnvelopeError`` with a
stable message and does not return a row.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

ENVELOPE_VERSION: Final = 1

REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "envelope_version",
    "url",
    "url_hash",
    "crawl_job_id",
    "job_id",
)
CRAWL_ID_FIELDS: Final[tuple[str, ...]] = ("crawl_job_id", "job_id")
OPTIONAL_FIELDS: Final[tuple[str, ...]] = (
    "parent_url",
    "content_hint",
    "priority",
    "status",
    "queued_at",
    "queued_by",
    "discovery_source",
    "target_stage",
    "is_heavy",
    "depth",
)
_INT_FIELDS: Final[frozenset[str]] = frozenset({"priority", "depth"})
_CONTRACT_FIELDS: Final[frozenset[str]] = frozenset(REQUIRED_FIELDS) | frozenset(OPTIONAL_FIELDS)
_URL_HASH: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{16,64}")


class Stage2UrlEnvelopeError(ValueError):
    """The stage1 -> stage2 URL envelope failed its versioned contract."""


def _version_error(got: object) -> Stage2UrlEnvelopeError:
    return Stage2UrlEnvelopeError(
        f"stage1->stage2 URL envelope version mismatch: got {got!r}, expected {ENVELOPE_VERSION}"
    )


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _canonicalize(record: Mapping[str, Any]) -> dict[str, Any]:
    """Required fields, then known optionals, then unknown keys sorted."""
    ordered: dict[str, Any] = {}
    for key in (*REQUIRED_FIELDS, *OPTIONAL_FIELDS):
        if key in record:
            ordered[key] = record[key]
    for key in sorted(record):
        if key not in ordered:
            ordered[key] = record[key]
    return ordered


def _field_errors(record: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for name in REQUIRED_FIELDS:
        if name == "envelope_version":
            continue
        value = record.get(name)
        if name not in record or (isinstance(value, str) and not value.strip()):
            errors.append(f"missing required field {name}")
            continue
        if not isinstance(value, str):
            errors.append(f"field {name} has type {type(value).__name__}, expected str")
            continue
        if name == "url_hash" and _URL_HASH.fullmatch(value) is None:
            errors.append("field url_hash must be 16-64 lowercase hex characters")

    for name in OPTIONAL_FIELDS:
        if name not in record:
            continue
        value = record[name]
        if name == "parent_url" and value is None:
            continue
        if name in _INT_FIELDS:
            if not _is_int(value):
                errors.append(f"field {name} has type {type(value).__name__}, expected int")
            continue
        if name == "is_heavy":
            if not isinstance(value, bool):
                errors.append(f"field {name} has type {type(value).__name__}, expected bool")
            continue
        if not isinstance(value, str):
            errors.append(f"field {name} has type {type(value).__name__}, expected str")
            continue
        if name == "target_stage" and value != "stage2":
            errors.append("field target_stage must be 'stage2'")
    return errors


def validate_stage2_url_envelope(record: Mapping[str, Any]) -> dict[str, Any]:
    """Check version, required fields and types. Keep unknown optional fields.

    Raises:
        Stage2UrlEnvelopeError: version is not ``ENVELOPE_VERSION``, or a
            required or known optional field has the wrong shape. The message
            is deterministic for a given record.
    """
    if not isinstance(record, Mapping):
        raise Stage2UrlEnvelopeError(f"stage1->stage2 URL envelope must be a mapping, got {type(record).__name__}")
    version = record.get("envelope_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != ENVELOPE_VERSION:
        raise _version_error(version)
    errors = _field_errors(record)
    if errors:
        raise Stage2UrlEnvelopeError("stage1->stage2 URL envelope invalid: " + "; ".join(sorted(errors)))
    return _canonicalize(record)
