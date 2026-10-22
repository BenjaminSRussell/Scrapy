"""Bulk reseed guardrails (#569): chunk pacing, Redis memory circuit, estimate, metrics.

Fake Redis only. No live server, compose, or scrape.
"""
from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

from src.lakehouse.lakehouse_manager import InMemoryBackend
from src.lakehouse.seed_manager import SEED_REDIS_DEPTH_KEY, SeedManager

pytestmark = pytest.mark.unit

URLS = [f"https://uconn.edu/page{i}" for i in range(5)]


class _Redis:
    """Just enough of redis.Redis for the depth and memory gate."""

    def __init__(self, depth: int = 0, used: int = 100, maxmemory: int = 1000):
        self.depth = depth
        self.used = used
        self.maxmemory = maxmemory

    def zcard(self, key):
        assert key == SEED_REDIS_DEPTH_KEY
        return self.depth

    def info(self, section):
        assert section == "memory"
        return {"used_memory": self.used, "maxmemory": self.maxmemory}


def _sample(name: str) -> float:
    return REGISTRY.get_sample_value(name) or 0.0


def _clear_watermark_env(monkeypatch) -> None:
    monkeypatch.delenv("REDIS_QUEUE_HIGH_WATERMARK", raising=False)
    monkeypatch.delenv("REDIS_QUEUE_LOW_WATERMARK", raising=False)
    monkeypatch.delenv("REDIS_QUEUE_BACKPRESSURE_MODE", raising=False)


def test_memory_watermark_must_be_a_ratio():
    with pytest.raises(ValueError):
        SeedManager(InMemoryBackend(), memory_watermark=0)
    with pytest.raises(ValueError):
        SeedManager(InMemoryBackend(), memory_watermark=1.5)


def test_memory_at_watermark_skips_enqueue(monkeypatch):
    _clear_watermark_env(monkeypatch)
    backend = InMemoryBackend()
    sm = SeedManager(backend, redis_client=_Redis(used=850, maxmemory=1000), write_domain_urls=False)
    before = _sample("seed_enqueue_throttled_total")
    result = sm.add_urls_to_seeds(URLS[:1], "seed", "manual", enqueue_stage2=True)
    assert result["seed_inserted"] == 0
    assert "seed_urls" not in backend.tables
    assert _sample("seed_enqueue_throttled_total") == before + 1


def test_memory_below_watermark_enqueues(monkeypatch):
    _clear_watermark_env(monkeypatch)
    backend = InMemoryBackend()
    sm = SeedManager(backend, redis_client=_Redis(used=849, maxmemory=1000), write_domain_urls=False)
    assert sm.add_urls_to_seeds(URLS[:1], "seed", "manual")["seed_inserted"] == 1


def test_no_maxmemory_never_trips_memory_gate(monkeypatch):
    _clear_watermark_env(monkeypatch)
    backend = InMemoryBackend()
    sm = SeedManager(backend, redis_client=_Redis(used=10**9, maxmemory=0), write_domain_urls=False)
    assert sm.add_urls_to_seeds(URLS[:1], "seed", "manual")["seed_inserted"] == 1


def test_bulk_reseed_rejects_chunks_while_memory_is_over_watermark(monkeypatch):
    _clear_watermark_env(monkeypatch)
    backend = InMemoryBackend()
    sm = SeedManager(backend, redis_client=_Redis(used=900, maxmemory=1000), write_domain_urls=False)
    rejected_before = _sample("seed_reseed_rejected_chunks_total")
    result = sm.bulk_seed_from_list(URLS, batch_size=2)
    assert result["rejected_chunks"] == 3
    assert result["seed_inserted"] == 0
    assert "seed_urls" not in backend.tables
    assert _sample("seed_reseed_rejected_chunks_total") == rejected_before + 3


def test_bulk_reseed_writes_all_chunks_when_gate_is_open(monkeypatch):
    _clear_watermark_env(monkeypatch)
    backend = InMemoryBackend()
    sm = SeedManager(backend, redis_client=_Redis(depth=0, used=100, maxmemory=1000), write_domain_urls=False)
    rate_before = _sample("seed_reseed_urls_total")
    result = sm.bulk_seed_from_list(URLS, batch_size=2)
    assert result["rejected_chunks"] == 0
    assert result["seed_inserted"] == len(URLS)
    assert _sample("seed_reseed_urls_total") == rate_before + len(URLS)


def test_bulk_reseed_paces_chunks_to_the_url_budget(monkeypatch):
    _clear_watermark_env(monkeypatch)
    sleeps: list[float] = []
    monkeypatch.setattr("src.lakehouse.seed_manager.time.sleep", sleeps.append)
    sm = SeedManager(InMemoryBackend(), redis_client=_Redis(), write_domain_urls=False)
    sm.bulk_seed_from_list(URLS, batch_size=2, max_urls_per_second=1000)
    assert len(sleeps) == 3
    assert all(0 <= s <= 2 / 1000 for s in sleeps)


def test_bulk_reseed_is_unpaced_by_default(monkeypatch):
    _clear_watermark_env(monkeypatch)
    sleeps: list[float] = []
    monkeypatch.setattr("src.lakehouse.seed_manager.time.sleep", sleeps.append)
    sm = SeedManager(InMemoryBackend(), redis_client=_Redis(), write_domain_urls=False)
    sm.bulk_seed_from_list(URLS, batch_size=2)
    assert sleeps == []


def test_estimate_projects_queue_depth_without_writing(monkeypatch):
    _clear_watermark_env(monkeypatch)
    backend = InMemoryBackend()
    sm = SeedManager(backend, redis_client=_Redis(depth=40, used=100, maxmemory=1000), write_domain_urls=False)
    est = sm.estimate_seed_load(URLS + ["ftp://bad", "https://uconn.edu/page0"], batch_size=2)
    assert est == {
        "valid_urls": 5,
        "rejected": 1,
        "chunks": 3,
        "current_queue_depth": 40,
        "projected_queue_depth": 45,
        "would_block": False,
    }
    assert "seed_urls" not in backend.tables


def test_estimate_reports_block_and_unknown_depth(monkeypatch):
    _clear_watermark_env(monkeypatch)
    blocked = SeedManager(InMemoryBackend(), redis_client=_Redis(used=900, maxmemory=1000), write_domain_urls=False)
    assert blocked.estimate_seed_load(URLS)["would_block"] is True

    class _Broken:
        def zcard(self, key):
            raise RuntimeError("redis down")

    unknown = SeedManager(InMemoryBackend(), redis_client=_Broken(), write_domain_urls=False)
    est = unknown.estimate_seed_load(URLS)
    assert est["current_queue_depth"] is None
    assert est["projected_queue_depth"] is None
    assert est["would_block"] is True
