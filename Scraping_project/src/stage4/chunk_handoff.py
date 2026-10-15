"""Stage 4 side of the stage 3 chunk handoff (#659).

``begin_stage4_work`` checks the handoff, including ``schema_version``,
before it calls ``on_chunk``. An empty ``chunks`` list is valid and calls
nothing. ``Stage4Worker`` still reads ``stage4_large_docs`` rows; chunk
work must enter through this function so a bad version cannot start it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from src.stage3.chunk_handoff import (
    CHUNK_FIELDS,
    MAX_CHUNK_CHARS,
    SCHEMA_VERSION,
    ChunkHandoffError,
    validate_handoff,
)

__all__ = [
    "CHUNK_FIELDS",
    "MAX_CHUNK_CHARS",
    "SCHEMA_VERSION",
    "ChunkHandoffError",
    "begin_stage4_work",
    "deserialize_handoff",
]


def deserialize_handoff(payload: str | bytes | dict[str, Any]) -> dict[str, Any]:
    """Parse JSON text or a dict into a validated handoff copy."""
    if isinstance(payload, dict):
        return validate_handoff(payload)
    if isinstance(payload, str | bytes):
        try:
            loaded: Any = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ChunkHandoffError("handoff is not valid JSON") from exc
        return validate_handoff(loaded)
    raise ChunkHandoffError("handoff must be a JSON object or text")


def begin_stage4_work(
    payload: str | bytes | dict[str, Any],
    on_chunk: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Validate, then run ``on_chunk`` once per chunk, in order.

    ``on_chunk`` is not called when validation fails, and not called for an
    empty chunk list. The returned object is the validated handoff.
    """
    envelope = deserialize_handoff(payload)
    chunks = envelope["chunks"]
    if not isinstance(chunks, list):
        raise ChunkHandoffError("chunks must be a list")
    for chunk in chunks:
        on_chunk(chunk)
    return envelope
