"""
Unit tests for Pydantic models.

Phase 9: Test data validation and type safety.
"""

import pytest
from datetime import datetime
from pydantic import ValidationError
from src.core.models import (
    URLRecord,
    Stage2Analysis,
    Stage3Summary,
    QueueRecord,
    ErrorRecord,
    PipelineMetrics
)


class TestURLRecord:
    """Test URLRecord model."""

    def test_valid_url_record(self):
        """Test valid URL record creation."""
        record = URLRecord(
            url="https://example.com",
            url_hash="abc123def456789012345678901234567890abcd"
        )
        assert record.url == "https://example.com"
        assert record.url_hash == "abc123def456789012345678901234567890abcd"

    def test_hash_validation(self):
        """Test hash validation."""
        with pytest.raises(ValidationError):
            URLRecord(
                url="https://example.com",
                url_hash="invalid_hash!"  # Non-hex characters
            )

    def test_hash_normalization(self):
        """Test hash is normalized to lowercase."""
        record = URLRecord(
            url="https://example.com",
            url_hash="ABC123DEF456789012345678901234567890ABCD"
        )
        assert record.url_hash == "abc123def456789012345678901234567890abcd"


class TestStage2Analysis:
    """Test Stage2Analysis model."""

    def test_valid_analysis(self):
        """Test valid analysis record."""
        record = Stage2Analysis(
            url="https://example.com",
            url_hash="abc123def456789012345678901234567890abcd",
            word_count=500,
            content_length=2500,
            html_length=5000,
            text_to_html_ratio=0.5,
            is_low_quality=False,
            is_massive_doc=False,
            quality_score=0.8,
            processed_at=datetime.now()
        )
        assert record.word_count == 500
        assert record.quality_score == 0.8

    def test_quality_score_validation(self):
        """Test quality score bounds."""
        with pytest.raises(ValidationError):
            Stage2Analysis(
                url="https://example.com",
                url_hash="abc123def456789012345678901234567890abcd",
                word_count=500,
                content_length=2500,
                html_length=5000,
                text_to_html_ratio=0.5,
                is_low_quality=False,
                is_massive_doc=False,
                quality_score=1.5,  # Invalid: > 1.0
                processed_at=datetime.now()
            )

    def test_keywords_filtering(self):
        """Test empty keywords are filtered."""
        record = Stage2Analysis(
            url="https://example.com",
            url_hash="abc123def456789012345678901234567890abcd",
            word_count=500,
            content_length=2500,
            html_length=5000,
            text_to_html_ratio=0.5,
            is_low_quality=False,
            is_massive_doc=False,
            quality_score=0.8,
            keywords=["valid", "", "  ", "another"],
            processed_at=datetime.now()
        )
        assert len(record.keywords) == 2
        assert "" not in record.keywords


class TestStage3Summary:
    """Test Stage3Summary model."""

    def test_summary_validation(self):
        """Test summary length validation."""
        with pytest.raises(ValidationError):
            Stage3Summary(
                url="https://example.com",
                url_hash="abc123def456789012345678901234567890abcd",
                summary="short",  # Too short
                word_count=100,
                quality_score=0.8,
                timestamp=datetime.now()
            )

    def test_valid_summary(self):
        """Test valid summary."""
        record = Stage3Summary(
            url="https://example.com",
            url_hash="abc123def456789012345678901234567890abcd",
            summary="This is a valid summary with enough content",
            word_count=100,
            quality_score=0.8,
            timestamp=datetime.now()
        )
        assert len(record.summary) > 10


class TestPipelineMetrics:
    """Test PipelineMetrics model."""

    def test_success_rate_calculation(self):
        """Test success rate property."""
        metrics = PipelineMetrics(
            stage="stage2",
            urls_processed=80,
            urls_failed=20,
            processing_time_seconds=100.0,
            average_latency_ms=50.0,
            throughput_per_minute=60.0,
            error_rate=0.2
        )
        assert metrics.success_rate == 0.8

    def test_zero_division_success_rate(self):
        """Test success rate with no data."""
        metrics = PipelineMetrics(
            stage="stage2",
            urls_processed=0,
            urls_failed=0,
            processing_time_seconds=0.0,
            average_latency_ms=0.0,
            throughput_per_minute=0.0,
            error_rate=0.0
        )
        assert metrics.success_rate == 0.0
