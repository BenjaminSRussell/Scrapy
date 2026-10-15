"""Validate the summary payload Stage 3 accepts from Stage 2 (#668)."""

from __future__ import annotations

import json
from typing import Any

from src.core.summary_payload_contract import SummaryPayloadError, checked_summary_payload

__all__ = ["SummaryPayloadError", "validate_summary_payload"]


def validate_summary_payload(payload: Any) -> dict[str, Any]:
    """Return a copy of a summary payload dict or JSON object.

    A ``str`` or ``bytes`` value is parsed as JSON first. Malformed JSON, a
    non-object, and a required field that is missing, null, or the wrong type
    raise SummaryPayloadError. Extra keys are kept.
    """
    if isinstance(payload, (str, bytes, bytearray)):
        payload = _decode_wire(payload)
    return checked_summary_payload(payload)


def _decode_wire(payload: str | bytes | bytearray) -> Any:
    if isinstance(payload, bytearray):
        payload = bytes(payload)
    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SummaryPayloadError(f"summary payload is malformed: invalid JSON: {exc}") from exc
    else:
        text = payload
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SummaryPayloadError(f"summary payload is malformed: invalid JSON: {exc.msg}") from exc
