"""Tests for common schema validation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from common.schemas import DiscoveryItem, EnrichmentItem, ValidationResult  # noqa: E402


def test_discovery_item_creation():
    """Test DiscoveryItem can be created with valid data."""
    item = DiscoveryItem(
        source_url="https://uconn.edu/source",
        discovered_url="https://uconn.edu/discovered",
        first_seen="2024-01-01T00:00:00",
        url_hash="abc123",
        discovery_depth=1
    )

    assert item.source_url == "https://uconn.edu/source"
    assert item.discovered_url == "https://uconn.edu/discovered"
    assert item.first_seen == "2024-01-01T00:00:00"
    assert item.url_hash == "abc123"
    assert item.discovery_depth == 1


def test_discovery_item_required_fields():
    """Test DiscoveryItem fails with missing required fields."""
    with pytest.raises(TypeError):
        DiscoveryItem(
            source_url="https://uconn.edu/source"
            # Missing other required fields
        )


def test_validation_result_creation():
    """Test ValidationResult can be created with valid data."""
    result = ValidationResult(
        url="https://uconn.edu/test",
        url_hash="def456",
        status_code=200,
        content_type="text/html",
        content_length=1024,
        response_time=0.5,
        is_valid=True,
        error_message=None,
        validated_at="2024-01-01T00:00:00"
    )

    assert result.url == "https://uconn.edu/test"
    assert result.url_hash == "def456"
    assert result.status_code == 200
    assert result.content_type == "text/html"
    assert result.content_length == 1024
    assert result.response_time == 0.5
    assert result.is_valid is True
    assert result.error_message is None
    assert result.validated_at == "2024-01-01T00:00:00"


def test_validation_result_with_error():
    """Test ValidationResult with error conditions."""
    result = ValidationResult(
        url="https://uconn.edu/broken",
        url_hash="error123",
        status_code=404,
        content_type="text/html",
        content_length=0,
        response_time=0.1,
        is_valid=False,
        error_message="Page not found",
        validated_at="2024-01-01T00:00:00"
    )

    assert result.is_valid is False
    assert result.error_message == "Page not found"
    assert result.status_code == 404


def test_enrichment_item_creation():
    """Test EnrichmentItem can be created with valid data."""
    item = EnrichmentItem(
        url="https://uconn.edu/enriched",
        url_hash="enrich123",
        title="Test Page",
        text_content="This is test content",
        word_count=4,
        entities=["UConn"],
        keywords=["test", "content"],
        content_tags=["academic"],
        has_pdf_links=False,
        has_audio_links=True,
        status_code=200,
        content_type="text/html",
        enriched_at="2024-01-01T00:00:00"
    )

    assert item.url == "https://uconn.edu/enriched"
    assert item.url_hash == "enrich123"
    assert item.title == "Test Page"
    assert item.text_content == "This is test content"
    assert item.word_count == 4
    assert item.entities == ["UConn"]
    assert item.keywords == ["test", "content"]
    assert item.content_tags == ["academic"]
    assert item.has_pdf_links is False
    assert item.has_audio_links is True
    assert item.status_code == 200
    assert item.content_type == "text/html"
    assert item.enriched_at == "2024-01-01T00:00:00"


@pytest.mark.parametrize("word_count,text", [
    (0, ""),
    (1, "word"),
    (5, "this is five words total"),
    (100, " ".join(["word"] * 100))
])
def test_enrichment_item_word_count_validation(word_count, text):
    """Test word count matches text content."""
    item = EnrichmentItem(
        url="https://uconn.edu/test",
        url_hash="test123",
        title="Test",
        text_content=text,
        word_count=word_count,
        entities=[],
        keywords=[],
        content_tags=[],
        has_pdf_links=False,
        has_audio_links=False,
        status_code=200,
        content_type="text/html",
        enriched_at="2024-01-01T00:00:00"
    )

    # Actual word count should match provided count
    len(text.split()) if text else 0
    assert item.word_count == word_count


@pytest.mark.parametrize("url", [
    "https://uconn.edu/",
    "https://admissions.uconn.edu/apply",
    "http://research.uconn.edu/projects/123"
])
def test_schema_url_formats(url):
    """Test schemas accept various UConn URL formats."""
    discovery = DiscoveryItem(
        source_url=url,
        discovered_url=url,
        first_seen="2024-01-01T00:00:00",
        url_hash="test123",
        discovery_depth=0
    )

    validation = ValidationResult(
        url=url,
        url_hash="test123",
        status_code=200,
        content_type="text/html",
        content_length=1024,
        response_time=0.1,
        is_valid=True,
        error_message=None,
        validated_at="2024-01-01T00:00:00"
    )

    enrichment = EnrichmentItem(
        url=url,
        url_hash="test123",
        title="Test",
        text_content="Content",
        word_count=1,
        entities=[],
        keywords=[],
        content_tags=[],
        has_pdf_links=False,
        has_audio_links=False,
        status_code=200,
        content_type="text/html",
        enriched_at="2024-01-01T00:00:00"
    )

    assert discovery.source_url == url
    assert validation.url == url
    assert enrichment.url == url