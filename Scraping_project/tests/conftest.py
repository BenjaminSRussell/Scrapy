"""Pytest fixtures for the scraping pipeline test suite.

K6 IMPLEMENTATION: Comprehensive fixtures for testing all pipeline components.
- Delta Lake sandbox (isolated test storage)
- PostgreSQL test database (separate from production)
- Playwright headless browser (for JS rendering tests)
- Tiny local HTTP server (for controlled test scenarios)
- Message queue fixtures (Redis-backed)
"""

import asyncio
import os
import shutil
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

try:  # pragma: no cover - optional dependency guard
    import redis  # type: ignore
except ImportError:  # pragma: no cover
    try:
        import fakeredis as redis  # type: ignore
    except ImportError:
        redis = None
# from playwright.async_api import async_playwright
# from scrapy.http import HtmlResponse, Request
from twisted.internet import reactor
from twisted.web import server, static

from src.common.config import Config
from src.common.delta_lake import DeltaLakeManager
from src.common.postgres_manager import PostgresManager

# ============================================================================
# Session-level fixtures (shared across all tests)
# ============================================================================

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def test_config() -> dict:
    """Load test configuration.

    Returns configuration with test-specific overrides.
    """
    config = Config.get_instance()
    # Override with test-specific settings
    config.set('delta_lake.base_path', './data/test_delta_lake')
    config.set('postgres.database', 'pipeline_metrics_test')
    return config.get_raw_config()


# ============================================================================
# Delta Lake fixtures (K6)
# ============================================================================

@pytest.fixture(scope="function")
def delta_sandbox(test_config) -> Generator[DeltaLakeManager, None, None]:
    """Create isolated Delta Lake sandbox for testing.

    K6: Provides clean Delta Lake storage for each test.
    - Creates temporary directory
    - Initializes DeltaLakeManager
    - Cleans up after test completes

    Yields:
        DeltaLakeManager configured for test isolation
    """
    # Use the factory to get an in-memory manager for tests
    from src.common.delta_lake import get_delta_manager
    yield get_delta_manager("memory")


@pytest.fixture(scope="function")
def delta_with_seed_urls(delta_sandbox) -> DeltaLakeManager:
    """Delta Lake sandbox pre-populated with seed URLs.

    K6: Useful for testing spiders that load from seed_urls table.
    """
    # Write test seed URLs
    seed_urls = [
        {'url': 'https://example.com', 'priority': 1, 'added_at': '2024-01-01T00:00:00'},
        {'url': 'https://example.com/page1', 'priority': 2, 'added_at': '2024-01-01T00:00:00'},
        {'url': 'https://example.com/page2', 'priority': 1, 'added_at': '2024-01-01T00:00:00'},
    ]

    # Use async_write=False since workers are disabled in tests
    delta_sandbox.write('seed_urls', seed_urls, mode='overwrite', async_write=False)

    return delta_sandbox


# ============================================================================
# PostgreSQL fixtures (K6)
# ============================================================================

@pytest.fixture(scope="session")
def postgres_test_db(test_config) -> Generator[PostgresManager, None, None]:
    """Create PostgreSQL test database.

    K6: Session-scoped test database that persists across tests.
    - Creates test database if it doesn't exist
    - Initializes schema
    - Drops database after session completes

    Yields:
        PostgresManager connected to test database
    """
    # Connect to default postgres database to create test DB
    pg_config = test_config['postgres']
    test_db_name = pg_config['database']

    postgres = PostgresManager(
        host=pg_config.get('host', 'localhost'),
        port=pg_config.get('port', 5432),
        database='postgres',  # Connect to default DB first
        user=pg_config.get('user', 'postgres'),
        password=pg_config.get('password', 'postgres')
    )

    try:
        # Create test database
        postgres.execute(f"DROP DATABASE IF EXISTS {test_db_name}")
        postgres.execute(f"CREATE DATABASE {test_db_name}")
        postgres.close()

        # Connect to test database
        test_postgres = PostgresManager(
            host=pg_config.get('host', 'localhost'),
            port=pg_config.get('port', 5432),
            database=test_db_name,
            user=pg_config.get('user', 'postgres'),
            password=pg_config.get('password', 'postgres')
        )

        # Initialize schema (create tables)
        test_postgres.initialize_schema()

        yield test_postgres

    finally:
        # Cleanup: drop test database
        test_postgres.close()
        postgres = PostgresManager(
            host=pg_config.get('host', 'localhost'),
            port=pg_config.get('port', 5432),
            database='postgres',
            user=pg_config.get('user', 'postgres'),
            password=pg_config.get('password', 'postgres')
        )
        postgres.execute(f"DROP DATABASE IF EXISTS {test_db_name}")
        postgres.close()


@pytest.fixture(scope="function")
def postgres_clean(postgres_test_db) -> PostgresManager:
    """PostgreSQL database with clean state.

    K6: Function-scoped fixture that truncates all tables before each test.
    """
    # Truncate all tables to ensure clean state
    tables = ['metrics', 'errors', 'spider_stats']  # Add your table names
    for table in tables:
        try:
            postgres_test_db.execute(f"TRUNCATE TABLE {table} CASCADE")
        except Exception:
            pass  # Table might not exist

    return postgres_test_db


# ============================================================================
# Playwright fixtures (K6)
# ============================================================================

# @pytest.fixture(scope="session")
# async def browser():
#     """Launch Playwright browser for JS rendering tests.
#
#     K6: Session-scoped headless browser for testing JS rendering.
#     """
#     async with async_playwright() as p:
#         browser = await p.chromium.launch(headless=True)
#         yield browser
#         await browser.close()
#
#
# @pytest.fixture(scope="function")
# async def page(browser):
#     """Create new browser page for test.
#
#     K6: Function-scoped page (isolated per test).
#     """
#     page = await browser.new_page()
#     yield page
#     await page.close()


# ============================================================================
# HTTP Server fixtures (K6)
# ============================================================================

@pytest.fixture(scope="session")
def http_server() -> Generator[tuple[str, int], None, None]:
    """Tiny local HTTP server for controlled test scenarios.

    K6: Serves static HTML files for testing spiders without external dependencies.

    Yields:
        Tuple of (host, port)
    """
    # Create temporary directory with test HTML files
    test_dir = tempfile.mkdtemp(prefix="http_test_")

    try:
        # Create test HTML files
        html_content = """
        <!DOCTYPE html>
        <html>
        <head><title>Test Page</title></head>
        <body>
            <h1>Test Content</h1>
            <a href="/page1">Page 1</a>
            <a href="/page2">Page 2</a>
            <a href="https://external.com">External Link</a>
            <img src="/image.jpg" alt="Test Image">
        </body>
        </html>
        """

        (Path(test_dir) / "index.html").write_text(html_content)
        (Path(test_dir) / "page1.html").write_text("<html><body><h1>Page 1</h1></body></html>")
        (Path(test_dir) / "page2.html").write_text("<html><body><h1>Page 2</h1></body></html>")

        # Create static file server
        root = static.File(test_dir)
        site = server.Site(root)
        port = 8888  # Fixed port for tests

        # Start server in background
        reactor.listenTCP(port, site)

        yield ("localhost", port)

    finally:
        # Cleanup
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir)


@pytest.fixture(scope="function")
def test_html_response() -> HtmlResponse:
    """Create mock Scrapy HtmlResponse for unit tests.

    K6: Pre-configured response for testing parsers without HTTP requests.
    """
    html = """
    <!DOCTYPE html>
    <html>
    <head><title>Test Page</title></head>
    <body>
        <h1>Test Content</h1>
        <a href="/page1">Page 1</a>
        <a href="/page2">Page 2</a>
        <a href="https://external.com">External</a>
        <script src="/app.js"></script>
        <img src="/image.jpg">
    </body>
    </html>
    """

    request = Request(url="https://example.com/test")
    response = HtmlResponse(
        url="https://example.com/test",
        body=html.encode('utf-8'),
        encoding='utf-8',
        request=request,
    )

    return response


# ============================================================================
# Redis/Queue fixtures (K6)
# ============================================================================

@pytest.fixture(scope="session")
def redis_client(test_config) -> Generator[Any, None, None]:
    """Create Redis client for test queue operations.

    K6: Session-scoped Redis client for message queue testing.
    """
    if redis is None:
        pytest.skip("Redis client not available; install redis or fakeredis")

    redis_config = test_config.get('redis', {})
    client = redis.Redis(
        host=redis_config.get('host', 'localhost'),
        port=redis_config.get('port', 6379),
        db=redis_config.get('db', 0) + 10,  # Use separate DB for tests
        decode_responses=True
    )

    yield client

    # Cleanup: flush test database
    client.flushdb()
    client.close()


@pytest.fixture(scope="function")
def redis_clean(redis_client) -> Any:
    """Clean Redis state for each test.

    K6: Function-scoped fixture that flushes test DB before each test.
    """
    redis_client.flushdb()
    return redis_client


@pytest.fixture(scope="function")
def mock_queue(redis_clean):
    """Mock message queue for testing.

    K6: Pre-configured queue with test messages.
    """
    # Add test messages to queue
    test_items = [
        {'url': 'https://example.com/1', 'depth': 0},
        {'url': 'https://example.com/2', 'depth': 1},
        {'url': 'https://example.com/3', 'depth': 1},
    ]

    for item in test_items:
        redis_clean.rpush('test_queue', str(item))

    return redis_clean


# ============================================================================
# Spider fixtures (K6)
# ============================================================================

@pytest.fixture(scope="function")
def mock_scrapy_settings() -> dict:
    """Mock Scrapy settings for spider tests.

    K6: Minimal settings dict for testing spiders in isolation.
    """
    return {
        'CONCURRENT_REQUESTS': 16,
        'DOWNLOAD_DELAY': 0,
        'DOWNLOAD_TIMEOUT': 10,
        'RETRY_TIMES': 0,  # Disable retries in tests
        'CLOSESPIDER_TIMEOUT': 60,  # Short timeout for tests
        'DEPTH_LIMIT': 3,
        'IGNORED_EXTENSIONS': ['.jpg', '.png', '.css', '.js'],
        'DELTA_BATCH_SIZE': 10,
    }


@pytest.fixture(scope="function")
def mock_spider_crawler(mock_scrapy_settings):
    """Mock Scrapy Crawler for spider initialization tests.

    K6: Allows testing spider.__init__() without full Scrapy engine.
    """
    from scrapy import Spider
    from scrapy.crawler import Crawler

    crawler = Crawler(Spider, settings=mock_scrapy_settings)
    return crawler


# ============================================================================
# Performance testing fixtures (K6)
# ============================================================================

@pytest.fixture(scope="function")
def performance_timer():
    """Timer fixture for performance tests.

    K6: Tracks execution time for performance benchmarks.

    Usage:
        with performance_timer() as timer:
            # Code to benchmark
            pass
        assert timer.elapsed < 1.0  # Assert < 1 second
    """
    import time

    class Timer:
        def __init__(self):
            self.start = None
            self.elapsed = None

        def __enter__(self):
            self.start = time.time()
            return self

        def __exit__(self, *args):
            self.elapsed = time.time() - self.start

    return Timer


# ============================================================================
# Markers and parametrization helpers
# ============================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')")
    config.addinivalue_line("markers", "integration: marks tests as integration tests")
    config.addinivalue_line("markers", "unit: marks tests as unit tests")
    config.addinivalue_line("markers", "component: marks tests as component tests")
    config.addinivalue_line("markers", "contract: marks tests as contract tests")
    config.addinivalue_line("markers", "performance: marks tests as performance tests")


# ============================================================================
# Singleton Reset Fixture
# ============================================================================

@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset singletons before each test to ensure isolation."""
    Config.reset_instance()
    DeltaLakeManager.reset_instance()
    PostgresManager.reset_instance()
