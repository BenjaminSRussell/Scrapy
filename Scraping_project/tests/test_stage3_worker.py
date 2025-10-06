"""Tests for Stage 3 worker."""

import pytest
from src.stage3.stage3_worker import Stage3Worker


@pytest.mark.asyncio
async def test_stage3_worker_initialization():
    """Test Stage 3 worker can be initialized."""
    worker = Stage3Worker(max_concurrent=5, batch_size=25)
    assert worker is not None
    assert worker.max_concurrent == 5
    assert worker.batch_size == 25


def test_fallback_summary():
    """Test fallback summary generation."""
    worker = Stage3Worker()

    # Short text
    text = "This is a short text."
    summary = worker._fallback_summary(text)
    assert summary == text

    # Long text
    long_text = "A" * 1000 + ". More text here."
    summary = worker._fallback_summary(long_text, max_chars=100)
    assert len(summary) <= 103  # 100 + "..."


def test_extract_key_facts():
    """Test key facts extraction."""
    worker = Stage3Worker()

    text = """
    Machine learning is a subset of AI. Deep learning uses neural networks.
    Python is popular for data science. TensorFlow is a ML framework.
    Natural language processing analyzes text.
    """

    keywords = ['machine learning', 'python', 'deep learning']
    facts = worker._extract_key_facts(text, keywords)

    assert isinstance(facts, list)
    assert len(facts) <= 5
    if facts:
        assert all(isinstance(f, str) for f in facts)
