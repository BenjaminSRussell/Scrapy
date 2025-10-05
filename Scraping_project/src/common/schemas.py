"""
Simple data schemas for pipeline.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class URLRecord:
    """URL discovery record."""
    url: str
    depth: int
    source_url: Optional[str] = None
    discovered_at: str = None

    def __post_init__(self):
        if self.discovered_at is None:
            self.discovered_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "depth": self.depth,
            "source_url": self.source_url,
            "discovered_at": self.discovered_at
        }


@dataclass
class ValidationResult:
    """URL validation result."""
    url: str
    is_valid: bool
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    error: Optional[str] = None
    validated_at: str = None

    def __post_init__(self):
        if self.validated_at is None:
            self.validated_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "is_valid": self.is_valid,
            "status_code": self.status_code,
            "content_type": self.content_type,
            "error": self.error,
            "validated_at": self.validated_at
        }


@dataclass
class EnrichedContent:
    """Enriched content record."""
    url: str
    title: Optional[str] = None
    content: Optional[str] = None
    keywords: list[str] = None
    classification: dict = None
    enriched_at: str = None

    def __post_init__(self):
        if self.keywords is None:
            self.keywords = []
        if self.classification is None:
            self.classification = {}
        if self.enriched_at is None:
            self.enriched_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "keywords": self.keywords,
            "classification": self.classification,
            "enriched_at": self.enriched_at
        }
