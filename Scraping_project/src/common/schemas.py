from dataclasses import dataclass
from typing import List, Optional


@dataclass
class DiscoveryItem:
    """Item returned by Stage 1 Discovery Spider"""
    source_url: str
    discovered_url: str
    first_seen: str
    discovery_depth: int


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
    error_message: Optional[str]
    validated_at: str
    learned_optimizations: Optional[List[str]] = None


@dataclass
class EnrichmentItem:
    """Item returned by Stage 3 Enrichment Spider"""
    url: str
    title: str
    text_content: str
    word_count: int
    entities: List[str]
    keywords: List[str]
    content_tags: List[str]
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
    source_url: Optional[str] = None
    first_seen: Optional[str] = None
    discovery_depth: Optional[int] = None

    # Validation info
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    content_length: Optional[int] = None
    response_time: Optional[float] = None
    is_valid: Optional[bool] = None
    validation_error: Optional[str] = None
    validated_at: Optional[str] = None

    # Enrichment info
    title: Optional[str] = None
    text_content: Optional[str] = None
    word_count: Optional[int] = None
    entities: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    content_tags: Optional[List[str]] = None
    has_pdf_links: Optional[bool] = None
    has_audio_links: Optional[bool] = None
    enriched_at: Optional[str] = None


@dataclass
class PipelineStats:
    """Statistics about pipeline execution"""
    stage: str
    start_time: str
    end_time: Optional[str] = None
    input_count: int = 0
    output_count: int = 0
    error_count: int = 0
    duration_seconds: Optional[float] = None