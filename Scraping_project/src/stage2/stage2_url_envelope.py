"""Stage 2 consumer for the versioned stage1 URL envelope (#667)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.core.stage2_url_envelope import Stage2UrlEnvelopeError, validate_stage2_url_envelope

__all__ = ["Stage2UrlEnvelopeError", "consume_stage2_url_record"]


def consume_stage2_url_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Accept a stage1 URL envelope or a pre-contract queue row.

    Rows with no ``envelope_version`` are returned unchanged (a shallow copy).
    Version ``1`` is checked for required fields and types; unknown optional
    fields are kept. Any other version raises ``Stage2UrlEnvelopeError``.

    Raises:
        Stage2UrlEnvelopeError: the record is not a mapping, the version is
            not 1, or a required or known optional field has the wrong shape.
    """
    if not isinstance(record, Mapping):
        raise Stage2UrlEnvelopeError(f"stage1->stage2 URL envelope must be a mapping, got {type(record).__name__}")
    if "envelope_version" not in record:
        return dict(record)
    return validate_stage2_url_envelope(record)
