"""Tests for Stage 2 worker."""

import pytest
from src.stage2.stage2_worker import Stage2Worker


@pytest.mark.asyncio
async def test_stage2_worker_initialization():
    """Test Stage 2 worker can be initialized."""
    worker = Stage2Worker(max_concurrent=10, batch_size=50)
    assert worker is not None
    assert worker.max_concurrent == 10
    assert worker.batch_size == 50


def test_quality_score_calculation():
    """Test quality score calculation."""
    worker = Stage2Worker()

    # Low quality
    score = worker._calculate_quality_score(word_count=10, text_ratio=0.05)
    assert score < 0.3

    # Medium quality
    score = worker._calculate_quality_score(word_count=500, text_ratio=0.2)
    assert 0.3 <= score <= 0.7

    # High quality
    score = worker._calculate_quality_score(word_count=1000, text_ratio=0.5)
    assert score > 0.7


def test_error_record_creation():
    """Test error record creation."""
    worker = Stage2Worker()

    error = worker._error_record(
        url='http://example.com',
        url_hash='abc123',
        error_code=404,
        error_msg='Not Found'
    )

    assert error['has_error'] is True
    assert error['error_code'] == 404
    assert error['url'] == 'http://example.com'
