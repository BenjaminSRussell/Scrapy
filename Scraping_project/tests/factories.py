"""Record builders for Stage 1–4 Delta tables (#275).

Each builder returns a plain ``dict`` whose keys match ``src.core.schemas``.
Pass keyword overrides for any field; unset fields get a deterministic default.
Existing fixtures such as ``sample_url_record`` / ``sample_stage2_data`` keep
working; new tests should prefer these factories.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _now() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def url_record(**overrides: Any) -> dict[str, Any]:
    """A Stage 1 discovery / seed-adjacent URL row."""
    base: dict[str, Any] = {
        "url": "https://example.com/test",
        "url_hash": "abc123def4567890",
        "is_heavy": False,
        "is_dynamic": False,
        "depth": 0,
        "parent_url": "https://example.com/",
        "status": "pending",
        "queued_at": _now(),
        "discovered_at": _now(),
    }
    base.update(overrides)
    return base


def stage2_record(**overrides: Any) -> dict[str, Any]:
    """A Stage 2 page-analysis row (``stage2_page_analysis`` / ``stage2_errors``)."""
    base: dict[str, Any] = {
        "url": "https://example.com/test",
        "url_hash": "abc123def4567890",
        "title": "Test Page",
        "word_count": 500,
        "content_length": 2500,
        "html_length": 5000,
        "text_to_html_ratio": 0.5,
        "is_low_quality": False,
        "is_massive_doc": False,
        "quality_score": 0.8,
        "text_content": "Sample content for Stage 2 analysis.",
        "keywords": ["test", "sample"],
        "has_error": False,
        "error_message": None,
        "error_code": None,
        "processed_at": _now(),
    }
    base.update(overrides)
    return base


def stage3_summary(**overrides: Any) -> dict[str, Any]:
    """A Stage 3 summary row (``stage3_summaries`` / ``stage3_queue``)."""
    base: dict[str, Any] = {
        "url": "https://example.com/test",
        "url_hash": "abc123def4567890",
        "summary": "A short summary of the page.",
        "word_count": 42,
        "keywords": ["summary", "test"],
        "quality_score": 0.75,
        "timestamp": _now(),
    }
    base.update(overrides)
    return base


def stage4_large_doc(**overrides: Any) -> dict[str, Any]:
    """A Stage 4 large-document summary row (``stage4_large_doc_summaries``)."""
    base: dict[str, Any] = {
        "url": "https://example.com/handbook.pdf",
        "url_hash": "pdf123def4567890",
        "summary": "Chapter summary for the large document.",
        "content_type": "application/pdf",
        "original_size": 1_000_000,
        "summary_size": 4_000,
        "compression_ratio": 0.004,
        "is_pdf": True,
        "processed_at": _now(),
    }
    base.update(overrides)
    return base


def stage4_chunk(**overrides: Any) -> dict[str, Any]:
    """One in-memory Stage 4 chunk: a ``chunk_spans()`` span plus its text.

    Stage 4 never persists chunk rows (only the merged summary in
    ``stage4_large_doc_summaries``), so this is not a table schema. It is the
    shape tests use when feeding ``LargeDocProcessor`` chunk-level helpers.
    """
    text = overrides.pop("text", "First chunk of the handbook.")
    start = overrides.pop("start", 0)
    base: dict[str, Any] = {
        "url": "https://example.com/handbook.pdf",
        "url_hash": "pdf123def4567890",
        "chunk_index": 0,
        "start": start,
        "end": start + len(text),
        "text": text,
    }
    base.update(overrides)
    return base
