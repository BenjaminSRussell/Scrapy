from __future__ import annotations
from dataclasses import dataclass


@dataclass
class DiscoveryItem:
    """Item returned by Stage 1 Discovery Spider"""
    source_url: str
    discovered_url: str
    first_seen: str
    discovery_depth: int
    discovery_source: str  # how was this URL found: 'html_link', 'ajax_endpoint', 'json_blob', 'pagination', 'sitemap'
    confidence: float  # 0.0-1.0 confidence score for dynamic discoveries


@dataclass
class ValidationResult:
    """Result from Stage 2 URL Validation"""
    url: str
    url_hash: str  # critical for stage linkage - without this everything breaks
    status_code: int
    content_type: str
    content_length: int
    response_time: float
    is_valid: bool
    error_message: str | None
    validated_at: str
    learned_optimizations: list[str] | None = None


@dataclass
class EnrichmentItem:
    """Item returned by Stage 3 Enrichment Spider"""
    url: str
    title: str
    text_content: str
    word_count: int
    entities: list[str]
    keywords: list[str]
    content_tags: list[str]
    has_pdf_links: bool
    has_audio_links: bool
    status_code: int
    content_type: str
    enriched_at: str


@dataclass
class URLRecord:
    """Complete record combining all stages"""
    url: str

    # Discovery info
    source_url: str | None = None
    first_seen: str | None = None
    discovery_depth: int | None = None

    # Validation info
    status_code: int | None = None
    content_type: str | None = None
    content_length: int | None = None
    response_time: float | None = None
    is_valid: bool | None = None
    validation_error: str | None = None
    validated_at: str | None = None

    # Enrichment info
    title: str | None = None
    text_content: str | None = None
    word_count: int | None = None
    entities: list[str] | None = None
    keywords: list[str] | None = None
    content_tags: list[str] | None = None
    has_pdf_links: bool | None = None
    has_audio_links: bool | None = None
    enriched_at: str | None = None


@dataclass
class PipelineStats:
    """Statistics about pipeline execution"""
    stage: str
    start_time: str
    end_time: str | None = None
    input_count: int = 0
    output_count: int = 0
    error_count: int = 0
    duration_seconds: float | None = None