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

    # StorageManager was removed (see PR #599: "StorageManager removed -
    # use get_delta() and get_redis() directly"). BaseSpider.__init__ now
    # calls get_delta()/get_redis() directly (imported at module scope in
    # src.stage1.experimental.base_spider, which src.stage1.base_spider
    # re-exports BaseSpider from), and uses redis_helper.client if present.
    fake_redis_helper = type("FakeRedisHelper", (), {"client": fake_redis})()

    monkeypatch.setattr("src.stage1.experimental.base_spider.get_delta", lambda: delta_with_seed_urls)
    monkeypatch.setattr("src.stage1.experimental.base_spider.get_redis", lambda: fake_redis_helper)

    spider = SeedSpider()

    assert len(spider.start_urls) == 3

    spider_again = SeedSpider()
    assert len(spider_again.start_urls) == 3
    assert spider_again.start_urls == spider.start_urls
