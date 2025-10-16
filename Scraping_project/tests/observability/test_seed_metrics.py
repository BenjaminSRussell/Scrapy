"""Tests for observability metrics related to the seed pipeline."""

from unittest.mock import MagicMock, patch

import pytest

from src.stage1.base_spider import BaseSpider


class SeedSpider(BaseSpider):
    """A minimal spider for testing seed loading observability."""

    name = "seed_spider"
    custom_settings = {"MAX_DEPTH": 0}


@pytest.mark.observability
@patch("src.stage1.base_spider.StorageManager")
@patch("src.stage1.base_spider.SEED_URLS_EMITTED_TOTAL")
@patch("src.stage1.base_spider.SEED_URLS_DEDUPLICATED_TOTAL")
@patch("src.stage1.base_spider.SEED_DEDUP_LATENCY_SECONDS")
def test_seed_deduplication_metrics_are_recorded(
    mock_latency_hist: MagicMock,
    mock_dedup_counter: MagicMock,
    mock_emitted_counter: MagicMock,
    mock_storage_manager_class: MagicMock,
):
    """
    Verify that seed deduplication metrics are correctly incremented and observed.
    """
    # Arrange: Set up mocks for storage and metrics
    mock_delta_instance = MagicMock()
    mock_redis_instance = MagicMock()

    seed_records = [
        {"url": "https://www.uconn.edu/foo"},
        {"url": "https://www.uconn.edu/bar"},
        {"url": "https://www.uconn.edu/foo"},
        {"url": "https://www.uconn.edu/baz"},
    ]
    mock_delta_instance.read.return_value = seed_records
    mock_redis_instance.scard.return_value = 0

    mock_storage_instance = MagicMock()
    mock_storage_instance.delta = mock_delta_instance
    mock_storage_instance.redis = mock_redis_instance
    mock_storage_manager_class.get_instance.return_value = mock_storage_instance

    # Act: Instantiate the spider, which triggers the instrumented logic
    SeedSpider.test_factory(name="metrics_test_spider")

    # Assert: Verify that the metric counters were incremented correctly
    # 4 raw URLs, 3 unique URLs -> 1 deduplicated
    mock_emitted_counter.labels.assert_called_once_with(spider="metrics_test_spider")
    mock_emitted_counter.labels.return_value.inc.assert_called_once_with(3)

    mock_dedup_counter.labels.assert_called_once_with(spider="metrics_test_spider")
    mock_dedup_counter.labels.return_value.inc.assert_called_once_with(1)

    # Assert: Verify that the latency histogram was observed
    mock_latency_hist.labels.assert_called_once_with(spider="metrics_test_spider")
    mock_latency_hist.labels.return_value.observe.assert_called_once()

    # Assert that the observed value is a float (non-negative)
    observed_latency = mock_latency_hist.labels.return_value.observe.call_args[0][0]
    assert isinstance(observed_latency, float)
    assert observed_latency >= 0