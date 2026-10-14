"""SeedManager skips enqueue when Redis depth is above the high watermark (#1105).

Fake Redis only. No live server, compose, or scrape.
"""
from __future__ import annotations

import pytest
import yaml
from prometheus_client import REGISTRY

from src.lakehouse.lakehouse_manager import InMemoryBackend
from src.lakehouse.seed_manager import (
    SEED_ENQUEUE_THROTTLED,
    SEED_REDIS_DEPTH_KEY,
    SeedManager,
    seed_enqueue_threshold,
)
from src.utils.redis import (
    DEFAULT_BACKPRESSURE_MODE,
    DEFAULT_QUEUE_HIGH_WATERMARK,
    DEFAULT_QUEUE_LOW_WATERMARK,
)
from src.utils.validation import default_config_path, validate_config_document

pytestmark = pytest.mark.unit

URLS = ["https://uconn.edu/page", "https://uconn.edu/other"]


class _Config:
    def __init__(self, value):
        self.value = value

    def get(self, key, default=None):
        if key == "stage1.seed_enqueue_depth_threshold":
            return self.value
        return default


def _metric() -> float:
    assert SEED_ENQUEUE_THROTTLED is not None
    return REGISTRY.get_sample_value("seed_enqueue_throttled_total") or 0.0


def _client():
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.FakeRedis(decode_responses=True)


def _fill(client, n: int) -> None:
    if n <= 0:
        return
    client.zadd(SEED_REDIS_DEPTH_KEY, {f"q{i}": i for i in range(n)})


def _clear_watermark_env(monkeypatch) -> None:
    monkeypatch.delenv("REDIS_QUEUE_HIGH_WATERMARK", raising=False)
    monkeypatch.delenv("REDIS_QUEUE_LOW_WATERMARK", raising=False)
    monkeypatch.delenv("REDIS_QUEUE_BACKPRESSURE_MODE", raising=False)


def test_shipped_threshold_is_the_high_watermark(monkeypatch):
    _clear_watermark_env(monkeypatch)
    assert DEFAULT_QUEUE_HIGH_WATERMARK == 10_000
    assert DEFAULT_QUEUE_LOW_WATERMARK == 8_000
    assert DEFAULT_BACKPRESSURE_MODE == "block"
    assert seed_enqueue_threshold() == 10_000
    text = default_config_path().read_text(encoding="utf-8")
    assert "seed_enqueue_depth_threshold: 10000" in text
    assert "default: 168" in text
    assert "stage1_errors: 720" in text
    assert "stage2_errors: 720" in text
    validate_config_document(yaml.safe_load(text))


def test_config_cannot_invent_a_second_threshold(monkeypatch):
    _clear_watermark_env(monkeypatch)
    assert seed_enqueue_threshold(_Config(50)) == 10_000
    assert seed_enqueue_threshold(_Config("nope")) == 10_000


def test_env_watermark_wins_when_config_disagrees(monkeypatch):
    monkeypatch.setenv("REDIS_QUEUE_HIGH_WATERMARK", "12000")
    assert seed_enqueue_threshold() == 12_000


def test_above_threshold_does_not_enqueue(monkeypatch):
    _clear_watermark_env(monkeypatch)
    client = _client()
    threshold = seed_enqueue_threshold()
    _fill(client, threshold + 1)
    backend = InMemoryBackend()
    sm = SeedManager(backend, redis_client=client, write_domain_urls=False)
    before = _metric()
    result = sm.add_urls_to_seeds(URLS, "seed", "scout", enqueue_stage2=True)
    assert result["seed_inserted"] == 0
    assert result["domain_inserted"] == 0
    assert result["stage2_enqueued"] == 0
    assert "seed_urls" not in backend.tables
    assert "stage2_queue" not in backend.tables
    assert _metric() == before + len(URLS)

    # Scout calls enqueue_stage2=False. That write is the enqueue this gate skips.
    scout = SeedManager(InMemoryBackend(), redis_client=client, write_domain_urls=False)
    scout_before = _metric()
    scout_result = scout.add_urls_to_seeds(URLS[:1], "seed", "scout", enqueue_stage2=False)
    assert scout_result["seed_inserted"] == 0
    assert scout_result["stage2_enqueued"] == 0
    assert _metric() == scout_before + 1


@pytest.mark.parametrize("depth", [0, 8_000, 10_000])
def test_at_or_under_threshold_enqueues(monkeypatch, depth):
    _clear_watermark_env(monkeypatch)
    client = _client()
    assert seed_enqueue_threshold() == 10_000
    _fill(client, depth)
    backend = InMemoryBackend()
    sm = SeedManager(backend, redis_client=client, write_domain_urls=False)
    before = _metric()
    result = sm.add_urls_to_seeds(URLS[:1], "seed", "scout", enqueue_stage2=True)
    assert result["seed_inserted"] == 1
    assert result["stage2_enqueued"] == 1
    assert len(backend.read("seed_urls")) == 1
    assert backend.read("stage2_queue")[0]["status"] == "pending"
    assert _metric() == before


def test_unreadable_redis_does_not_enqueue(monkeypatch):
    _clear_watermark_env(monkeypatch)

    class _Broken:
        def zcard(self, key):
            raise RuntimeError("redis down")

    backend = InMemoryBackend()
    sm = SeedManager(backend, redis_client=_Broken(), write_domain_urls=False)
    before = _metric()
    result = sm.add_urls_to_seeds(URLS[:1], "seed", "manual", enqueue_stage2=True)
    assert result["seed_inserted"] == 0
    assert result["domain_inserted"] == 0
    assert result["stage2_enqueued"] == 0
    assert "seed_urls" not in backend.tables
    assert "stage2_queue" not in backend.tables
    assert _metric() == before + 1


def test_missing_client_is_not_depth_zero(monkeypatch):
    """A failed connect is unknown depth, not an empty queue."""
    _clear_watermark_env(monkeypatch)
    monkeypatch.delenv("OBS_OFFLINE", raising=False)
    monkeypatch.setattr("src.lakehouse.seed_manager._connect_depth_client", lambda: None)
    backend = InMemoryBackend()
    sm = SeedManager(backend, write_domain_urls=False)
    before = _metric()
    result = sm.add_urls_to_seeds(URLS[:1], "seed", "manual", enqueue_stage2=True)
    assert result["seed_inserted"] == 0
    assert result["stage2_enqueued"] == 0
    assert "seed_urls" not in backend.tables
    assert _metric() == before + 1
