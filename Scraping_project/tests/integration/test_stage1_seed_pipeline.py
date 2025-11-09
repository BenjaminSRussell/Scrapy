"""Integration tests for Stage 1 spider bootstrapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from src.stage1.base_spider import BaseSpider

@dataclass
class RedisSetStub:

    existing: set[str]

    def pipeline(self):
        return self

    def sismember(self, key, value):
        self.last_key = key
        self.sismember_calls = getattr(self, "sismember_calls", []) + [value]

    def sadd(self, key, value):
        self.existing.add(value)

    def execute(self):
        if hasattr(self, "sismember_calls"):
            results = [value in self.existing for value in self.sismember_calls]
            del self.sismember_calls
            return results
        return []

    def scard(self, key):
        return len(self.existing)

class SeedSpider(BaseSpider):
    name = "seed_spider"
    custom_settings: dict[bool | float | int | str | None, Any] = {}

@pytest.mark.integration
def test_seed_urls_deduplicated(delta_with_seed_urls, monkeypatch):
    fake_redis = RedisSetStub(existing=set())

    class MockStorageManager:
        def __init__(self):
            self.delta = delta_with_seed_urls
            self.postgres = object()
            self.redis = fake_redis

        @classmethod
        def get_instance(cls):
            return cls()

    monkeypatch.setattr("src.stage1.base_spider.StorageManager", MockStorageManager)

    spider = SeedSpider()

    assert len(spider.start_urls) == 3

    spider_again = SeedSpider()
    assert len(spider_again.start_urls) == 3
    assert spider_again.start_urls == spider.start_urls
