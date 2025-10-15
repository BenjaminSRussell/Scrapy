"""Integration tests for Stage 1 spider bootstrapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from src.stage1.base_spider import BaseSpider


@dataclass
class RedisSetStub:
    """Minimal Redis stub for BaseSpider seed deduplication."""

    existing: set[str]

    def pipeline(self):
        return self

    def sismember(self, key, value):
        self.last_key = key
        self.sismember_calls = getattr(self, "sismember_calls", []) + [value]

    def sadd(self, key, value):
        self.existing.add(value)

    def execute(self):
        # First execute answers membership checks
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

    monkeypatch.setattr(
        "src.stage1.base_spider.get_delta_manager", lambda: delta_with_seed_urls
    )
    monkeypatch.setattr("src.stage1.base_spider.get_postgres_manager", lambda: object())
    monkeypatch.setattr(
        "src.stage1.base_spider.redis.Redis", lambda **kwargs: fake_redis
    )

    spider = SeedSpider()

    assert len(spider.start_urls) == 3

    # Run again with Redis already populated to verify deduplication
    monkeypatch.setattr(
        "src.stage1.base_spider.redis.Redis", lambda **kwargs: fake_redis
    )
    spider_again = SeedSpider()
    assert spider_again.start_urls == []
