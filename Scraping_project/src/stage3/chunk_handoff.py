"""Stage 3 → Stage 4 chunk handoff (#659).

Stage 3 writes one JSON object per document. Stage 4 reads it back with
``src.stage4.chunk_handoff`` and rejects any other ``schema_version``
before per-chunk work. Chunk rows are not a Delta table; the wire shape
is this object.

``MAX_CHUNK_CHARS`` is the stage 4 window (``LargeDocProcessor.CHUNK_SIZE``).
A chunk may be empty (``text == ""`` and ``start == end``) or exactly that
long. Longer text is rejected.
"""

from __future__ import annotations

import json
from typing import Any, TypeGuard

# Bump only when the fields below change in a way Stage 4 cannot read.
SCHEMA_VERSION = 1

# Same bound as LargeDocProcessor.CHUNK_SIZE in stage4/large_doc_processor.py.
MAX_CHUNK_CHARS = 5000

ENVELOPE_FIELDS = ("chunks", "schema_version", "source_url", "source_url_hash")

# name -> accepted JSON type. Integers are ``int`` only (bool is rejected).
CHUNK_FIELDS: dict[str, type] = {
    "schema_version": int,
    "chunk_id": str,
    "chunk_index": int,
    "source_url": str,
    "source_url_hash": str,
    "text": str,
    "start": int,
    "end": int,
}


class ChunkHandoffError(ValueError):
    """Handoff rejected. Stage 4 must not start per-chunk work."""


def make_chunk(
    *,
    source_url: str,
    source_url_hash: str,
    chunk_index: int,
    text: str,
    start: int,
    end: int,
    chunk_id: str | None = None,
) -> dict[str, Any]:
    """One chunk dict. ``chunk_id`` defaults to ``{source_url_hash}:{chunk_index}``."""
    return {
        "schema_version": SCHEMA_VERSION,
        "chunk_id": f"{source_url_hash}:{chunk_index}" if chunk_id is None else chunk_id,
        "chunk_index": chunk_index,
        "source_url": source_url,
        "source_url_hash": source_url_hash,
        "text": text,
        "start": start,
        "end": end,
    }


def serialize_handoff(source_url: str, source_url_hash: str, chunks: list[dict[str, Any]]) -> str:
    """Canonical JSON (sorted keys, trailing newline) for a validated handoff."""
    envelope = validate_handoff(
        {
            "schema_version": SCHEMA_VERSION,
            "source_url": source_url,
            "source_url_hash": source_url_hash,
            "chunks": chunks,
        }
    )
    return json.dumps(envelope, indent=2, sort_keys=True) + "\n"


def validate_handoff(payload: Any) -> dict[str, Any]:
    """Return a copy of a valid handoff. Check ``schema_version`` first."""
    if not isinstance(payload, dict):
        raise ChunkHandoffError("handoff must be a JSON object")
    _require_version(payload.get("schema_version"))
    if set(payload) != set(ENVELOPE_FIELDS):
        missing = sorted(set(ENVELOPE_FIELDS) - set(payload))
        extra = sorted(set(payload) - set(ENVELOPE_FIELDS))
        raise ChunkHandoffError(f"handoff fields mismatch; missing={missing} extra={extra}")
    source_url = _require_token(payload.get("source_url"), "source_url")
    source_url_hash = _require_token(payload.get("source_url_hash"), "source_url_hash")
    raw_chunks = payload.get("chunks")
    if not isinstance(raw_chunks, list):
        raise ChunkHandoffError("chunks must be a list")
    chunks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_chunks):
        chunk = _validate_chunk(raw, index=index, source_url=source_url, source_url_hash=source_url_hash)
        chunk_id = chunk["chunk_id"]
        if chunk_id in seen:
            raise ChunkHandoffError(f"duplicate chunk_id {chunk_id!r}")
        seen.add(chunk_id)
        chunks.append(chunk)
    return {
        "schema_version": SCHEMA_VERSION,
        "source_url": source_url,
        "source_url_hash": source_url_hash,
        "chunks": chunks,
    }


def _is_int(value: Any) -> TypeGuard[int]:
    """JSON integers only. ``bool`` is an ``int`` subclass and is rejected."""
    return isinstance(value, int) and not isinstance(value, bool)


def _require_version(value: Any) -> None:
    if not _is_int(value) or value != SCHEMA_VERSION:
        raise ChunkHandoffError(f"unsupported schema_version {value!r}; expected {SCHEMA_VERSION}")


def _require_int(value: Any, field: str) -> int:
    if not _is_int(value):
        raise ChunkHandoffError(f"{field} must be an int")
    return value


def _require_token(value: Any, field: str) -> str:
    if not isinstance(value, str) or value == "" or value != value.strip():
        raise ChunkHandoffError(f"{field} must be a non-empty string")
    return value


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ChunkHandoffError(f"{field} must be a string")
    if len(value) > MAX_CHUNK_CHARS:
        raise ChunkHandoffError(f"{field} length {len(value)} exceeds boundary {MAX_CHUNK_CHARS}")
    return value


def _validate_chunk(
    raw: Any,
    *,
    index: int,
    source_url: str,
    source_url_hash: str,
) -> dict[str, Any]:
    where = f"chunks[{index}]"
    if not isinstance(raw, dict):
        raise ChunkHandoffError(f"{where} must be an object")
    _require_version(raw.get("schema_version"))
    if set(raw) != set(CHUNK_FIELDS):
        missing = sorted(set(CHUNK_FIELDS) - set(raw))
        extra = sorted(set(raw) - set(CHUNK_FIELDS))
        raise ChunkHandoffError(f"{where} fields mismatch; missing={missing} extra={extra}")
    chunk_index = _require_int(raw.get("chunk_index"), f"{where}.chunk_index")
    if chunk_index != index:
        raise ChunkHandoffError(f"{where}.chunk_index must be {index} to keep order, got {chunk_index}")
    text = _require_text(raw.get("text"), f"{where}.text")
    start = _require_int(raw.get("start"), f"{where}.start")
    end = _require_int(raw.get("end"), f"{where}.end")
    if start < 0 or end < start or end - start != len(text):
        raise ChunkHandoffError(f"{where} span {start}:{end} does not match text length {len(text)}")
    chunk_source = _require_token(raw.get("source_url"), f"{where}.source_url")
    chunk_hash = _require_token(raw.get("source_url_hash"), f"{where}.source_url_hash")
    if chunk_source != source_url or chunk_hash != source_url_hash:
        raise ChunkHandoffError(f"{where} source reference does not match the handoff")
    chunk_id = _require_token(raw.get("chunk_id"), f"{where}.chunk_id")
    return {
        "schema_version": SCHEMA_VERSION,
        "chunk_id": chunk_id,
        "chunk_index": chunk_index,
        "source_url": chunk_source,
        "source_url_hash": chunk_hash,
        "text": text,
        "start": start,
        "end": end,
    }
