"""Unit tests for experimental BaseSpider init (#374).

Verifies that BaseSpider constructs with get_config/get_delta/get_redis
mocks and does not raise NameError from removed ConfigManager/StorageManager.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.unit
@pytest.mark.stage1
class TestExperimentalBaseSpiderInit:
    """BaseSpider __init__ must not reference ConfigManager/StorageManager."""

    @patch("src.stage1.experimental.base_spider.URLProcessor")
    @patch("src.stage1.experimental.base_spider.get_redis")
    @patch("src.stage1.experimental.base_spider.get_delta")
    @patch("src.stage1.experimental.base_spider.get_config")
    def test_init_with_mocks_does_not_raise_nameerror(
        self,
        mock_get_config,
        mock_get_delta,
        mock_get_redis,
        mock_url_processor,
    ):
        mock_config = MagicMock()
        mock_config.get.side_effect = lambda key, default=None: {
            "stage1.js_confidence_threshold": 0.5,
            "stage1.batch_size": 50,
        }.get(key, default)
        mock_get_config.return_value = mock_config

        mock_delta = MagicMock()
        mock_delta.read.return_value = [{"url": "https://uconn.edu/"}]
        mock_get_delta.return_value = mock_delta

        mock_redis_helper = MagicMock()
        mock_redis_client = MagicMock()
        mock_redis_client.scard.return_value = 0
        mock_redis_helper.client = mock_redis_client
        mock_get_redis.return_value = mock_redis_helper

        mock_url_processor.return_value = MagicMock()

        # Import after patches are active on the module under test
        from src.stage1.experimental.base_spider import BaseSpider

        spider = BaseSpider.test_factory(name="test_base_374")

        assert spider.name == "test_base_374"
        assert spider.config is mock_config
        assert spider.delta is mock_delta
        assert spider.redis_client is mock_redis_client
        assert spider.js_confidence_threshold == 0.5
        assert spider.batch_size == 50
        assert spider.start_urls == ["https://uconn.edu/"]
        mock_get_config.assert_called_once()
        mock_get_delta.assert_called_once()
        mock_get_redis.assert_called_once()

    @patch("src.stage1.experimental.base_spider.URLProcessor")
    @patch("src.stage1.experimental.base_spider.get_redis")
    @patch("src.stage1.experimental.base_spider.get_delta")
    @patch("src.stage1.experimental.base_spider.get_config")
    def test_init_has_no_configmanager_or_storagemanager_attrs(
        self,
        mock_get_config,
        mock_get_delta,
        mock_get_redis,
        mock_url_processor,
    ):
        mock_config = MagicMock()
        mock_config.get.side_effect = lambda key, default=None: default
        mock_get_config.return_value = mock_config
        mock_delta = MagicMock()
        mock_delta.read.return_value = []
        mock_get_delta.return_value = mock_delta
        mock_redis_helper = MagicMock()
        mock_redis_helper.client = MagicMock()
        mock_redis_helper.client.scard.return_value = 0
        mock_get_redis.return_value = mock_redis_helper
        mock_url_processor.return_value = MagicMock()

        from src.stage1.experimental.base_spider import BaseSpider

        spider = BaseSpider.test_factory(name="test_base_374b")

        assert not hasattr(spider, "config_manager")
        assert not hasattr(spider, "storage")
