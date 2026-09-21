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


@pytest.fixture
def delta_sandbox(temp_dir) -> Generator:
    """Real DeltaLakeManager backed by a throwaway temp directory."""
    from src.lakehouse.lakehouse_manager import DeltaLakeManager

    manager = DeltaLakeManager(base_path=str(temp_dir), start_workers=False)
    yield manager
    manager.shutdown()


@pytest.fixture
def delta_with_seed_urls(delta_sandbox):
    """delta_sandbox pre-populated with a seed_urls table."""
    delta_sandbox.write(
        "seed_urls",
        [
            {"url": "https://uconn.edu/", "url_hash": "seed0", "source": "test"},
            {"url": "https://uconn.edu/research", "url_hash": "seed1", "source": "test"},
            {"url": "https://uconn.edu/admissions", "url_hash": "seed2", "source": "test"},
        ],
        mode="overwrite",
        async_write=False,
    )
    return delta_sandbox


@pytest.fixture
def redis_clean():
    """Fresh FakeRedis instance, flushed before and after the test."""
    from fakeredis import FakeRedis

    client = FakeRedis(decode_responses=True)
    client.flushall()
    yield client
    client.flushall()


@pytest.fixture
def redis_client(redis_clean):
    """Alias of redis_clean for tests using the older fixture name."""
    return redis_clean


@pytest.fixture
def mock_scrapy_settings():
    """Real (empty) Scrapy Settings object."""
    from scrapy.settings import Settings

    return Settings()


@pytest.fixture
def mock_spider_crawler(mock_scrapy_settings):
    """Minimal crawler stub for Spider.from_crawler()/_set_crawler().

    Avoids scrapy.utils.test.get_crawler(), which instantiates a real
    Crawler and requires a Twisted reactor to be installed - that
    conflicts with pytest-asyncio's own event loop management.
    """
    from unittest.mock import MagicMock

    crawler = MagicMock()
    crawler.settings = mock_scrapy_settings
    crawler.signals = MagicMock()
    return crawler


@pytest.fixture
def test_html_response():
    """HtmlResponse with a stable set of links for extract_links()-style tests."""
    from scrapy.http import HtmlResponse, Request

    html = """
    <html>
        <body>
            <a href="/page1">Page 1</a>
            <a href="/page2">Page 2</a>
            <a href="https://external.com/">External</a>
            <a href="/image.jpg">Image</a>
            <a href="/script.js">Script</a>
            <a href="/style.css">Style</a>
        </body>
    </html>
    """
    request = Request(url="https://example.com/index.html", meta={"depth": 0})
    return HtmlResponse(
        url="https://example.com/index.html",
        body=html.encode("utf-8"),
        encoding="utf-8",
        request=request,
        headers={"Content-Type": "text/html; charset=utf-8"},
    )


class _TimingResult:
    """Holds the elapsed time from one `with performance_timer as result:` block."""

    def __init__(self):
        self.elapsed = None


class _PerformanceTimer:
    """Context manager factory: each `with performance_timer as x:` gets its
    own _TimingResult, so multiple timed blocks in the same test (e.g.
    comparing timer_small vs timer_large) don't share state.
    """

    def __enter__(self):
        import time

        self._start = time.perf_counter()
        self._result = _TimingResult()
        return self._result

    def __exit__(self, exc_type, exc_val, exc_tb):
        import time

        self._result.elapsed = time.perf_counter() - self._start
        return False


@pytest.fixture
def performance_timer():
    """Usable as `with performance_timer as timer: ...; timer.elapsed`."""
    return _PerformanceTimer()


@pytest.fixture
def postgres_clean():
    """Real PostgresManager, truncated before and after the test.

    Skips (rather than being marked skip) when no Postgres is reachable,
    matching PostgresManager's own graceful-degradation design - CI's
    postgres service container makes this a real integration test there.
    """
    import os

    from src.utils.postgres import POSTGRES_AVAILABLE, PostgresManager

    if not POSTGRES_AVAILABLE:
        pytest.skip("psycopg2 not installed")

    try:
        manager = PostgresManager(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
            database=os.getenv("DB_NAME", "scraping_pipeline"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "postgres"),
        )
    except Exception as exc:
        pytest.skip(f"Postgres not reachable: {exc}")

    tables = "spider_stats, performance_metrics, error_logs, error_analysis_reports"
    manager.execute(f"TRUNCATE {tables}")
    yield manager
    manager.execute(f"TRUNCATE {tables}")
    manager.close()
