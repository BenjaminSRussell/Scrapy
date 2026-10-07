"""Tests for direct storage backend access.

StorageManager (a facade wrapping delta/postgres/redis behind one
lazy-initializing object) was deliberately removed - see PR #599:
"StorageManager removed - use get_delta() and get_redis() directly".
Postgres was never part of that replacement; it remains a Phase 6
placeholder everywhere in the pipeline (stage2_worker.py,
stage3_worker.py), so there is no storage.postgres equivalent to test.
"""

from src.core.config import get_config
from src.utils.delta import DeltaHelper, get_delta, reset_delta
from src.utils.redis import RedisHelper, get_redis, reset_redis

class TestGetDelta:

    def teardown_method(self):
        reset_delta()

    def test_returns_singleton(self):
        delta1 = get_delta()
        delta2 = get_delta()
        assert delta1 is delta2

    def test_returns_delta_helper(self):
        delta = get_delta()
        assert isinstance(delta, DeltaHelper)
        assert hasattr(delta, "base_path")
        assert hasattr(delta, "write")
        assert hasattr(delta, "read")

class TestGetRedis:

    def teardown_method(self):
        reset_redis()

    def test_returns_singleton(self):
        redis1 = get_redis()
        redis2 = get_redis()
        assert redis1 is redis2

    def test_returns_redis_helper(self):
        redis = get_redis()
        assert isinstance(redis, RedisHelper)

class TestGetConfig:

    def test_returns_config_with_sections(self):
        config = get_config()
        assert config.get_section("redis") is not None
        assert config.get_section("stages") is not None

class TestDeltaWriteAndRead:

    def teardown_method(self):
        reset_delta()

    def test_delta_write_and_read(self, delta_sandbox):
        test_records = [
            {"url": "https://example.com/1", "status": "pending"},
            {"url": "https://example.com/2", "status": "pending"},
        ]

        delta_sandbox.write("test_table", test_records, async_write=False)

        records = delta_sandbox.read("test_table")
        assert len(records) >= 2

class TestAllBackendsAccessible:

    def teardown_method(self):
        reset_delta()
        reset_redis()

    def test_all_backends_accessible(self):
        assert get_delta() is not None
        assert get_redis() is not None
        assert get_config() is not None
