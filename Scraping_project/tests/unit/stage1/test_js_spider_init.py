"""Unit tests for experimental JavaScriptSpider init (#375).

Verifies that JavaScriptSpider constructs with get_config() (and mocked
Delta/Redis/SeedManager/JSPriorityQueue) and does not raise NameError from
the removed ConfigManager reference.

Note: the missing `src.stage1.base_spider` import path is issue #376 and is
stubbed here only so this test can import the module under test.
"""

import inspect
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# Stub missing src.stage1.base_spider before any import of js_spider (#376 OOS).
if "src.stage1.base_spider" not in sys.modules:
    _stub = ModuleType("src.stage1.base_spider")

    class _StubBaseSpider:
        @staticmethod
        def normalize_url(url: str) -> str:
            return url

    _stub.BaseSpider = _StubBaseSpider
    sys.modules["src.stage1.base_spider"] = _stub


@pytest.mark.unit
@pytest.mark.stage1
class TestJavaScriptSpiderInit:
    """JavaScriptSpider __init__ must use get_config(), not ConfigManager."""

    @patch("src.stage1.experimental.js_spider.JSPriorityQueue")
    @patch("src.stage1.experimental.js_spider.SeedManager")
    @patch("src.utils.redis.get_redis")
    @patch("src.stage1.experimental.js_spider.get_delta")
    @patch("src.stage1.experimental.js_spider.get_config")
    def test_init_with_mocks_does_not_raise_nameerror(
        self,
        mock_get_config,
        mock_get_delta,
        mock_get_redis,
        mock_seed_manager,
        mock_priority_queue_cls,
    ):
        mock_config = MagicMock()
        mock_config.get.side_effect = lambda key, default=None: {
            "stage1.js_queue_batch_size": 50,
            "stage1.js_confidence_threshold": 0.5,
            "redis.host": "localhost",
        }.get(key, default)
        mock_get_config.return_value = mock_config

        mock_delta = MagicMock()
        mock_delta.read.return_value = [
            {"url": "https://uconn.edu/js-page", "status": "pending"},
            {"url": "https://uconn.edu/done", "status": "completed"},
        ]
        mock_get_delta.return_value = mock_delta

        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        mock_seed_manager.return_value = MagicMock()

        mock_queue = MagicMock()
        mock_queue.get_stats.return_value = {"total_size": 0}
        mock_priority_queue_cls.return_value = mock_queue

        from src.stage1.experimental.js_spider import JavaScriptSpider

        spider = JavaScriptSpider()

        assert spider.name == "javascript"
        assert spider.config is mock_config
        assert spider.delta is mock_delta
        assert spider.start_urls == ["https://uconn.edu/js-page"]
        # Config fields resolve via get_config() / config.get(...)
        assert spider.config.get("stage1.js_queue_batch_size", 0) == 50
        assert spider.config.get("redis.host") == "localhost"
        mock_get_config.assert_called_once()
        mock_get_delta.assert_called_once()
        mock_get_redis.assert_called_once()
        mock_seed_manager.assert_called_once_with(mock_delta)
        mock_priority_queue_cls.assert_called_once()

    @patch("src.stage1.experimental.js_spider.JSPriorityQueue")
    @patch("src.stage1.experimental.js_spider.SeedManager")
    @patch("src.utils.redis.get_redis")
    @patch("src.stage1.experimental.js_spider.get_delta")
    @patch("src.stage1.experimental.js_spider.get_config")
    def test_init_source_has_no_configmanager(
        self,
        mock_get_config,
        mock_get_delta,
        mock_get_redis,
        mock_seed_manager,
        mock_priority_queue_cls,
    ):
        mock_config = MagicMock()
        mock_config.get.side_effect = lambda key, default=None: default
        mock_get_config.return_value = mock_config
        mock_delta = MagicMock()
        mock_delta.read.return_value = []
        mock_get_delta.return_value = mock_delta
        mock_get_redis.return_value = MagicMock()
        mock_seed_manager.return_value = MagicMock()
        mock_queue = MagicMock()
        mock_queue.get_stats.return_value = {"total_size": 0}
        mock_priority_queue_cls.return_value = mock_queue

        from src.stage1.experimental import js_spider as js_mod
        from src.stage1.experimental.js_spider import JavaScriptSpider

        spider = JavaScriptSpider()

        assert spider.config is mock_config
        source = inspect.getsource(js_mod)
        assert "ConfigManager" not in source
        assert "get_config()" in source
