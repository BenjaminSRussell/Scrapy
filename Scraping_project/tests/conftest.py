import asyncio
import contextlib
import os
import shutil
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any

import fakeredis
import pytest

try:
    import redis  # type: ignore
except ImportError:
    try:
        import fakeredis as redis  # type: ignore
    except ImportError:
        redis = None
from scrapy.http import HtmlResponse, Request
from twisted.internet import reactor
from twisted.web import server, static

from src.common.config import Config
from src.common.delta_lake import DeltaLakeManager, InMemoryDeltaManager
from src.common.postgres_manager import PostgresManager

# ============================================================================
# ============================================================================

def _patch_fakeredis_helpers():
    try:
        import fakeredis._helpers as helpers

        original_get_args_to_warn = helpers._get_args_to_warn

        def patched_get_args_to_warn() -> set[str]:
            try:
                return original_get_args_to_warn()
            except AttributeError:
                return set()

        helpers._get_args_to_warn = patched_get_args_to_warn
    except Exception:
        pass

_patch_fakeredis_helpers()

# ============================================================================
# ============================================================================

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session")
def test_config() -> dict:
    config = Config.get_instance()
    config.set("delta_lake.base_path", "./data/test_delta_lake")
    config.set("postgres.database", "scraping_pipeline_test")
    return config.get_raw_config()

# ============================================================================
# ============================================================================

@pytest.fixture(scope="function")
def delta_sandbox(
    test_config,
) -> Generator[DeltaLakeManager | InMemoryDeltaManager, None, None]:
    """Create isolated Delta Lake sandbox for testing.

    K6: Provides clean Delta Lake storage for each test.
    - Creates temporary directory
    - Initializes DeltaLakeManager
    - Cleans up after test completes

    Yields:
        DeltaLakeManager configured for test isolation
    """
    from src.common.delta_lake import get_delta_manager

    yield get_delta_manager("memory")

@pytest.fixture(scope="function")
def delta_with_seed_urls(
    delta_sandbox,
) -> DeltaLakeManager | InMemoryDeltaManager:
    """Delta Lake sandbox pre-populated with seed URLs.

    K6: Useful for testing spiders that load from seed_urls table.
    """
    seed_urls = [
        {
            "url": "https://example.com",
            "priority": 1,
            "added_at": "2024-01-01T00:00:00",
        },
        {
            "url": "https://example.com/page1",
            "priority": 2,
            "added_at": "2024-01-01T00:00:00",
        },
        {
            "url": "https://example.com/page2",
            "priority": 1,
            "added_at": "2024-01-01T00:00:00",
        },
    ]

    delta_sandbox.write("seed_urls", seed_urls, mode="overwrite", async_write=False)

    return delta_sandbox

# ============================================================================
# ============================================================================

@pytest.fixture(scope="session")
def postgres_test_db(test_config) -> Generator[PostgresManager, None, None]:
    pg_config = test_config["postgres"]
    test_db_name = pg_config["database"]

    postgres = PostgresManager(
        host=pg_config.get("host", "localhost"),
        port=pg_config.get("port", 5432),
        database="postgres",
        user=pg_config.get("user", "postgres"),
        password=pg_config.get("password", "postgres"),
    )

    try:
        postgres.execute(f"DROP DATABASE IF EXISTS {test_db_name}")
        postgres.execute(f"CREATE DATABASE {test_db_name}")
        postgres.close()

        test_postgres = PostgresManager(
            host=pg_config.get("host", "localhost"),
            port=pg_config.get("port", 5432),
            database=test_db_name,
            user=pg_config.get("user", "postgres"),
            password=pg_config.get("password", "postgres"),
        )

        test_postgres.initialize_schema()

        yield test_postgres

    finally:
        test_postgres.close()
        postgres = PostgresManager(
            host=pg_config.get("host", "localhost"),
            port=pg_config.get("port", 5432),
            database="postgres",
            user=pg_config.get("user", "postgres"),
            password=pg_config.get("password", "postgres"),
        )
        postgres.execute(f"DROP DATABASE IF EXISTS {test_db_name}")
        postgres.close()

@pytest.fixture(scope="function")
def postgres_clean(postgres_test_db) -> PostgresManager:
    tables = ["metrics", "errors", "spider_stats"]
    for table in tables:
        try:
            postgres_test_db.execute(f"TRUNCATE TABLE {table} CASCADE")
        except Exception:
            pass

    return postgres_test_db

# ============================================================================
# ============================================================================

# ============================================================================
# ============================================================================

@pytest.fixture(scope="session")
def http_server() -> Generator[tuple[str, int], None, None]:
    test_dir = tempfile.mkdtemp(prefix="http_test_")

    try:
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

        root = static.File(test_dir)
        site = server.Site(root)
        port = 8888

        reactor.listenTCP(port, site)

        yield ("localhost", port)

    finally:
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir)

@pytest.fixture(scope="function")
def test_html_response() -> HtmlResponse:
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
        body=html.encode("utf-8"),
        encoding="utf-8",
        request=request,
    )

    return response

# ============================================================================
# ============================================================================

@pytest.fixture(autouse=True, scope="session")
def _force_test_redis_defaults():
    use_real = os.getenv("USE_REAL_REDIS") == "1"
    if not use_real:
        os.environ.setdefault("REDIS_URL", "fakeredis://")
    yield

@pytest.fixture(scope="function")
def redis_client(monkeypatch):
    # ---- Adjust these imports to your codebase ----
    try:
        from src.common import redis_manager  # noqa: F401
    except Exception:
        pass

    fake = fakeredis.FakeStrictRedis(decode_responses=True)

    try:
        import redis

        monkeypatch.setattr(redis, "StrictRedis", lambda *a, **k: fake, raising=True)
        monkeypatch.setattr(redis, "Redis", lambda *a, **k: fake, raising=True)
    except Exception:
        pass

    yield fake

    with contextlib.suppress(Exception):
        fake.flushall()

@pytest.fixture(scope="function")
def redis_clean(redis_client) -> Any:
    redis_client.flushall()
    return redis_client

@pytest.fixture(scope="function")
def mock_queue(redis_clean):
    test_items = [
        {"url": "https://example.com/1", "depth": 0},
        {"url": "https://example.com/2", "depth": 1},
        {"url": "https://example.com/3", "depth": 1},
    ]

    for item in test_items:
        redis_clean.rpush("test_queue", str(item))

    return redis_clean

# ============================================================================
# ============================================================================

@pytest.fixture(scope="function")
def mock_scrapy_settings() -> dict:
    return {
        "CONCURRENT_REQUESTS": 16,
        "DOWNLOAD_DELAY": 0,
        "DOWNLOAD_TIMEOUT": 10,
        "RETRY_TIMES": 0,
        "CLOSESPIDER_TIMEOUT": 60,
        "DEPTH_LIMIT": 3,
        "IGNORED_EXTENSIONS": [".jpg", ".png", ".css", ".js"],
        "DELTA_BATCH_SIZE": 10,
    }

@pytest.fixture(scope="function")
def mock_spider_crawler(mock_scrapy_settings):
    from scrapy import Spider
    from scrapy.crawler import Crawler

    crawler = Crawler(Spider, settings=mock_scrapy_settings)
    return crawler

# ============================================================================
# ============================================================================

@pytest.fixture(scope="function")
def performance_timer():
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

    return Timer()

# ============================================================================
# ============================================================================

def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')")
    config.addinivalue_line("markers", "integration: marks tests as integration tests")
    config.addinivalue_line("markers", "unit: marks tests as unit tests")
    config.addinivalue_line("markers", "component: marks tests as component tests")
    config.addinivalue_line("markers", "contract: marks tests as contract tests")
    config.addinivalue_line("markers", "performance: marks tests as performance tests")

# ============================================================================
# ============================================================================

@pytest.fixture(autouse=True)
def reset_singletons():
    Config.reset_instance()
    DeltaLakeManager.reset_instance()
    PostgresManager.reset_instance()
