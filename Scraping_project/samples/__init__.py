"""Reusable sample builders shared between tests and runtime utilities."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Dict, Any, Optional

from scrapy.http import Request, HtmlResponse

from common.schemas import DiscoveryItem, ValidationResult, EnrichmentItem

ISO_START = datetime(2024, 1, 1, 12, 0, 0)


def iso_timestamp(offset: int = 0) -> str:
    """Return deterministic ISO timestamp offset by seconds."""
    return (ISO_START + timedelta(seconds=offset)).isoformat()


def build_discovery_item(**overrides: Any) -> DiscoveryItem:
    """Create a DiscoveryItem with sensible defaults."""
    defaults = {
        "source_url": "https://uconn.edu/source",
        "discovered_url": "https://uconn.edu/page",
        "first_seen": iso_timestamp(),
        "url_hash": "hash_0001",
        "discovery_depth": 1,
    }
    defaults.update(overrides)
    return DiscoveryItem(**defaults)


def build_validation_result(**overrides: Any) -> ValidationResult:
    """Create a ValidationResult with defaults."""
    defaults = {
        "url": "https://uconn.edu/page",
        "url_hash": "hash_val_0001",
        "status_code": 200,
        "content_type": "text/html; charset=utf-8",
        "content_length": 1024,
        "response_time": 0.2,
        "is_valid": True,
        "error_message": None,
        "validated_at": iso_timestamp(60),
    }
    defaults.update(overrides)
    return ValidationResult(**defaults)


def build_enrichment_item(**overrides: Any) -> EnrichmentItem:
    """Create an EnrichmentItem for enrichment pipeline tests."""
    defaults = {
        "url": "https://uconn.edu/enriched",
        "url_hash": "hash_enr_0001",
        "title": "Sample Title",
        "text_content": "Sample body text about UConn admissions and research.",
        "word_count": 8,
        "entities": ["UConn"],
        "keywords": ["admissions", "research"],
        "content_tags": ["admissions"],
        "has_pdf_links": False,
        "has_audio_links": False,
        "status_code": 200,
        "content_type": "text/html; charset=utf-8",
        "enriched_at": iso_timestamp(120),
    }
    defaults.update(overrides)
    return EnrichmentItem(**defaults)


def html_response(
    url: str,
    html: str,
    *,
    depth: int = 0,
    first_seen: Optional[str] = None,
    request_kwargs: Optional[Dict[str, Any]] = None,
) -> HtmlResponse:
    """Return HtmlResponse with Request carrying required meta data."""
    meta = {
        "source_url": url,
        "depth": depth,
        "first_seen": first_seen or iso_timestamp(depth),
    }
    request = Request(url=url, meta=meta, **(request_kwargs or {}))
    return HtmlResponse(url=url, body=html.encode("utf-8"), encoding="utf-8", request=request)


def write_jsonl(path: Path, data: Iterable[Dict[str, Any]]) -> None:
    """Helper to write iterable of dicts to JSONL for fixture setup."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for entry in data:
            fp.write(json.dumps(entry, ensure_ascii=False) + "\n")


def discovery_items_to_dicts(items: Iterable[DiscoveryItem]) -> List[Dict[str, Any]]:
    return [asdict(item) for item in items]

