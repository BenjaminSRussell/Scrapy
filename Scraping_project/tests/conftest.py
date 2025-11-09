"""
Pytest configuration and fixtures for comprehensive testing.

Phase 9: Testing infrastructure with reusable fixtures.
"""

import pytest
import asyncio
from pathlib import Path
from typing import Generator, AsyncGenerator
from unittest.mock import Mock
import tempfile
import shutil

# Async support
@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Temporary directory for test files."""
    temp_path = Path(tempfile.mkdtemp())
    yield temp_path
    shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture
def mock_redis():
    """Mock Redis client for testing."""
    from fakeredis import FakeRedis
    return FakeRedis()


@pytest.fixture
def mock_delta_helper(temp_dir):
    """Mock Delta Lake helper."""
    from unittest.mock import MagicMock
    mock = MagicMock()
    mock.base_path = str(temp_dir)
    mock.read.return_value = []
    mock.write.return_value = True
    return mock


@pytest.fixture
async def sample_url_record():
    """Sample URL record for testing."""
    from datetime import datetime
    return {
        "url": "https://example.com/test",
        "url_hash": "abc123def456789012345678901234567890abcd",
        "discovered_at": datetime.now(),
        "status": "pending",
        "depth": 0
    }


@pytest.fixture
def sample_stage2_data():
    """Sample Stage 2 analysis data."""
    from datetime import datetime
    return {
        "url": "https://example.com/test",
        "url_hash": "abc123def456789012345678901234567890abcd",
        "title": "Test Page",
        "word_count": 500,
        "content_length": 2500,
        "html_length": 5000,
        "text_to_html_ratio": 0.5,
        "is_low_quality": False,
        "is_massive_doc": False,
        "quality_score": 0.8,
        "text_content": "Sample content",
        "keywords": ["test", "sample"],
        "has_error": False,
        "error_message": None,
        "error_code": None,
        "processed_at": datetime.now()
    }


@pytest.fixture
def mock_circuit_breaker():
    """Mock circuit breaker for testing."""
    from src.utils.retry import CircuitBreaker
    return CircuitBreaker(
        failure_threshold=3,
        recovery_timeout=10,
        name="test"
    )
