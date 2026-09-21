"""Unit tests for RedisHelper (src/utils/redis.py).

Replaces the old RedisManager test suite - see the module docstring in
tests/integration/test_redis_integration.py for why the queue-push/pop
methods it tested don't exist anymore.
"""

from unittest.mock import MagicMock, patch

import pytest
import redis

from src.utils.redis import RedisHelper

class TestRedisHelperInit:

    @pytest.mark.unit
    def test_init_stores_connection_params(self):
        helper = RedisHelper(host="127.0.0.1", port=6380, db=2, password="secret")

        assert helper.host == "127.0.0.1"
        assert helper.port == 6380
        assert helper.db == 2
        assert helper.password == "secret"
        assert helper._client is None

    @pytest.mark.unit
    def test_client_is_lazy(self):
        helper = RedisHelper()
        assert helper._client is None

    @pytest.mark.unit
    def test_client_connection_failure_raises(self):
        helper = RedisHelper(host="127.0.0.1", port=1)

        with patch("redis.Redis") as mock_redis_cls:
            mock_redis_cls.return_value.ping.side_effect = redis.exceptions.ConnectionError
            with pytest.raises(redis.exceptions.ConnectionError):
                _ = helper.client

class TestRedisHelperUrlDedup:

    @pytest.mark.unit
    @pytest.mark.redis
    def test_mark_and_check_url_seen(self, redis_clean):
        helper = RedisHelper()
        helper._client = redis_clean

        url = "https://example.com/test"
        assert not helper.check_url_seen(url, "scout")

        helper.mark_url_seen(url, "scout")
        assert helper.check_url_seen(url, "scout")

    @pytest.mark.unit
    @pytest.mark.redis
    def test_different_prefixes_are_isolated(self, redis_clean):
        helper = RedisHelper()
        helper._client = redis_clean

        url = "https://example.com/test"
        helper.mark_url_seen(url, "scout")

        assert not helper.check_url_seen(url, "deep_dive")

class TestRedisHelperSets:

    @pytest.mark.unit
    @pytest.mark.redis
    def test_add_to_set_and_get_members(self, redis_clean):
        helper = RedisHelper()
        helper._client = redis_clean

        added = helper.add_to_set("my_set", "a", "b", "c")
        assert added == 3
        assert helper.get_set_members("my_set") == {"a", "b", "c"}
        assert helper.get_set_size("my_set") == 3

class TestRedisHelperCounters:

    @pytest.mark.unit
    @pytest.mark.redis
    def test_increment_counter_default_amount(self, redis_clean):
        helper = RedisHelper()
        helper._client = redis_clean

        assert helper.increment_counter("hits") == 1
        assert helper.increment_counter("hits") == 2

    @pytest.mark.unit
    @pytest.mark.redis
    def test_increment_counter_custom_amount(self, redis_clean):
        helper = RedisHelper()
        helper._client = redis_clean

        assert helper.increment_counter("hits", amount=5) == 5

    @pytest.mark.unit
    @pytest.mark.redis
    def test_get_counter_defaults_to_zero(self, redis_clean):
        helper = RedisHelper()
        helper._client = redis_clean

        assert helper.get_counter("never_set") == 0

class TestRedisHelperCircuitBreaker:

    @pytest.mark.unit
    @pytest.mark.redis
    def test_open_and_check_circuit(self, redis_clean):
        helper = RedisHelper()
        helper._client = redis_clean

        assert not helper.is_circuit_open("bad.example.com")

        helper.open_circuit("bad.example.com", duration_seconds=60, reason="timeout")

        assert helper.is_circuit_open("bad.example.com")

    @pytest.mark.unit
    @pytest.mark.redis
    def test_get_open_circuits_lists_all(self, redis_clean):
        helper = RedisHelper()
        helper._client = redis_clean

        helper.open_circuit("a.example.com")
        helper.open_circuit("b.example.com")

        open_circuits = helper.get_open_circuits()
        assert set(open_circuits) == {"a.example.com", "b.example.com"}

class TestRedisHelperMaintenance:

    @pytest.mark.unit
    @pytest.mark.redis
    def test_delete_key(self, redis_clean):
        helper = RedisHelper()
        helper._client = redis_clean

        helper.mark_url_seen("https://example.com", "x")
        assert helper.delete_key("x:urls") is True
        assert not helper.check_url_seen("https://example.com", "x")

    @pytest.mark.unit
    @pytest.mark.redis
    def test_get_key_count(self, redis_clean):
        helper = RedisHelper()
        helper._client = redis_clean

        assert helper.get_key_count() == 0
        helper.mark_url_seen("https://example.com", "x")
        assert helper.get_key_count() == 1

    @pytest.mark.unit
    @pytest.mark.redis
    def test_clear_all(self, redis_clean):
        helper = RedisHelper()
        helper._client = redis_clean

        helper.mark_url_seen("https://example.com", "x")
        assert helper.clear_all() is True
        assert helper.get_key_count() == 0

    @pytest.mark.unit
    @pytest.mark.redis
    def test_ping(self, redis_clean):
        helper = RedisHelper()
        helper._client = redis_clean

        assert helper.ping() is True
