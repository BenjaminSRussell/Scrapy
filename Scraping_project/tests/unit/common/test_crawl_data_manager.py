"""Unit tests for crawl_data_manager delta factory (#294)."""

from src.utils.delta import get_delta, get_delta_manager
from src.common.crawl_data_manager import CrawlDataManager


def test_get_delta_and_alias_are_same_factory():
    assert get_delta is get_delta_manager


def test_crawl_data_manager_accepts_injected_delta():
    class FakeDelta:
        def read(self, *args, **kwargs):
            return []

        def count(self, *args, **kwargs):
            return 0

    mgr = CrawlDataManager(delta_manager=FakeDelta(), lookback_days=7)
    assert mgr.delta_manager is not None
    assert mgr._read_table_safe("missing") == []
