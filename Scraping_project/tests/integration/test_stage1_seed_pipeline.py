"""Integration tests for Stage 1 spider bootstrapping."""

from unittest.mock import MagicMock, patch

import pytest

from src.stage1.base_spider import BaseSpider


class SeedSpider(BaseSpider):
    """A minimal spider for testing seed loading."""

    name = "seed_spider"
    custom_settings = {"MAX_DEPTH": 0}


@pytest.mark.integration
@patch("src.stage1.base_spider.StorageManager")
def test_seed_urls_are_loaded_and_deduplicated(mock_storage_manager_class: MagicMock):
    """
    Verify that BaseSpider correctly loads seed URLs from Delta Lake,
    deduplicates them, and populates the start_urls attribute.
    """
    # Arrange: Create mocks for the storage backends.
    mock_delta_instance = MagicMock()
    mock_redis_instance = MagicMock()

    # Arrange: Configure the mock DeltaLakeManager to return records with duplicates.
    seed_records = [
        {"url": "https://www.uconn.edu/foo"},
        {"url": "https://www.uconn.edu/bar"},
        {"url": "https://www.uconn.edu/foo"},  # Duplicate URL
    ]
    mock_delta_instance.read.return_value = seed_records

    # Arrange: The spider's __init__ calls redis.scard, so we mock its return value.
    mock_redis_instance.scard.return_value = 0

    # Arrange: Configure the StorageManager mock. When the spider calls
    # StorageManager.get_instance(), it will receive our mock storage object.
    mock_storage_instance = MagicMock()
    mock_storage_instance.delta = mock_delta_instance
    mock_storage_instance.redis = mock_redis_instance
    mock_storage_manager_class.get_instance.return_value = mock_storage_instance

    # Act: Instantiate the spider. This will trigger the seed loading logic.
    spider = SeedSpider.test_factory()

    # Assert: Verify that the `read` method on the delta mock was called correctly.
    mock_delta_instance.read.assert_called_once_with("seed_urls")

    # Assert: Verify that start_urls contains the raw, non-deduplicated list.
    # This assertion is expected to FAIL against the *fixed* code, but PASS against
    # the current code. We will then fix the code and make this test pass with
    # proper deduplication. For now, we confirm the test is wired correctly.
    # Let's start by making it fail on purpose to prove the wiring.
    expected_unique_urls = ["https://www.uconn.edu/bar", "https://www.uconn.edu/foo"]
    assert sorted(spider.start_urls) == sorted(expected_unique_urls)