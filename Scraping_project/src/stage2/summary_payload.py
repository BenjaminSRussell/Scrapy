"""Serialize the summary payload Stage 2 hands to Stage 3 (#668)."""

from __future__ import annotations

from typing import Any

from src.core.summary_payload_contract import SummaryPayloadError, checked_summary_payload

__all__ = ["SummaryPayloadError", "serialize_summary_payload"]


def serialize_summary_payload(record: Any) -> dict[str, Any]:
    """Return a copy of a Stage 2 analysis row that Stage 3 can score.

    Raises SummaryPayloadError when a required count, source identifier, or
    content field is missing, null, blank, or the wrong type. Extra keys are
    kept, including fields Stage 3 does not read.
    """
    return checked_summary_payload(record)
