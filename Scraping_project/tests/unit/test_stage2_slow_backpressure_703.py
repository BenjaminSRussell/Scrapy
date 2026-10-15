"""#703: queue backpressure when stage 2 consumes slower than stage 1 produces.

Deterministic fakes. No Redis server, Kafka, Scrapy crawl, or docker.
The orchestrator's stage workers are replaced; the real watermark gate
(``admit_enqueue``) is the queue adapter under ``StageQueueHandoff``.
"""

from __future__ import annotations

import asyncio

import pytest

from src.orchestrator import pipeline_orchestrator as po
from src.orchestrator.stage_queue import StageQueueHandoff
from src.utils.redis import queue_watermark_settings, reset_queue_backpressure

pytestmark = pytest.mark.unit

_QUEUE = "stage2_slow_703"
_STALL = "stage2_stall_703"


def _blocks(queue: str) -> float:
    from prometheus_client import REGISTRY

    return (
        REGISTRY.get_sample_value(
            "redis_queue_backpressure_total", {"queue": queue, "action": "block"}
        )
        or 0.0
    )


@pytest.fixture
def watermarks(monkeypatch):
    monkeypatch.setenv("REDIS_QUEUE_HIGH_WATERMARK", "4")
    monkeypatch.setenv("REDIS_QUEUE_LOW_WATERMARK", "1")
    monkeypatch.setenv("REDIS_QUEUE_BACKPRESSURE_MODE", "block")
    monkeypatch.setenv("REDIS_QUEUE_BACKPRESSURE_SLEEP_SECONDS", "0.5")
    monkeypatch.setenv("REDIS_QUEUE_BACKPRESSURE_MAX_BLOCK_SECONDS", "30")
    reset_queue_backpressure()
    yield
    reset_queue_backpressure()


class _FakeDelta:
    def count(self, _name: str) -> int:
        return 0

    def read(self, _name: str, **_kwargs: object) -> list:
        return []


def _clock():
    now = {"t": 0.0}

    def clock() -> float:
        return now["t"]

    def advance(seconds: float) -> None:
        now["t"] += seconds

    return clock, advance


def test_slow_stage2_blocks_and_resumes_without_loss_or_duplication(watermarks, monkeypatch, tmp_path):
    """Stage 1 fills a capped queue; a slow stage 2 unblocks it one row at a time."""
    cfg = queue_watermark_settings()
    assert (cfg["high"], cfg["low"], cfg["mode"]) == (4, 1, "block")
    high = int(cfg["high"])

    delivered: list[str] = []
    sleeps: list[float] = []
    depths: list[int] = []
    clock, advance = _clock()
    handoff = StageQueueHandoff(_QUEUE, clock=clock)
    before_blocks = _blocks(_QUEUE)

    def sleeper(seconds: float) -> None:
        # Stage 2 is behind: each backoff takes a single row, not the backlog.
        sleeps.append(seconds)
        advance(seconds)
        depths.append(handoff.qsize())
        item = handoff.get()
        depths.append(handoff.qsize())
        if item is not None:
            delivered.append(item["url"])

    handoff.sleeper = sleeper

    urls = [f"https://uconn.edu/p{i}" for i in range(12)]
    inits: list[dict] = []

    def stage1(url_limit: int | None = None, spider_name: str = "scout") -> int:
        for url in urls:
            assert handoff.put(url, {"url": url}) is True
            assert handoff.qsize() <= high
        # A producer retry of a URL stage 2 already took must not queue a copy.
        assert handoff.put(urls[0], {"url": urls[0]}) is True
        assert handoff.qsize() <= high
        return handoff.qsize()

    class SlowStage2:
        def __init__(self, **kwargs: object) -> None:
            inits.append(dict(kwargs))

        async def run(self) -> dict[str, int]:
            n = 0
            while True:
                item = handoff.get()
                if item is None:
                    break
                delivered.append(item["url"])
                n += 1
                assert handoff.qsize() <= high
            return {"analyzed": n, "quality_docs": n, "massive_docs": 0}

    class Idle:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def run(self) -> int:
            return 0

    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))
    monkeypatch.setattr(po, "get_delta", lambda: _FakeDelta())
    monkeypatch.setattr(po, "Stage2Worker", SlowStage2)
    monkeypatch.setattr(po, "Stage3Worker", Idle)
    monkeypatch.setattr(po, "Stage4Worker", Idle)
    orch = po.PipelineOrchestrator()
    monkeypatch.setattr(orch, "run_stage1", stage1)

    stats = asyncio.run(orch.run_full_pipeline(stage1_url_limit=len(urls), stage2_concurrent=3))

    assert stats.status == "complete"
    assert inits and inits[0].get("max_concurrent") == 3
    assert sleeps, "producer must block or back off while stage 2 is slow"
    assert handoff.block_events == len(sleeps)
    assert _blocks(_QUEUE) > before_blocks
    assert depths and max(depths) <= high
    assert handoff.peak <= high
    assert handoff.qsize() == 0
    assert delivered == urls
    assert len(delivered) == len(set(delivered))


def test_stalled_stage2_does_not_accumulate_and_catchup_is_exact(watermarks, monkeypatch):
    """A stopped consumer refuses further rows instead of buffering them."""
    monkeypatch.setenv("REDIS_QUEUE_BACKPRESSURE_MAX_BLOCK_SECONDS", "1")
    high = 4
    delivered: list[str] = []
    sleeps: list[float] = []
    drain = {"on": False}
    clock, advance = _clock()

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        advance(seconds)
        if not drain["on"]:
            return
        item = handoff.get()
        if item is not None:
            delivered.append(item["url"])

    handoff = StageQueueHandoff(_STALL, sleeper=sleeper, clock=clock)
    held: list[str] = []

    first = [f"https://uconn.edu/s{i}" for i in range(high)]
    for url in first:
        assert handoff.put(url, {"url": url}) is True
    assert handoff.qsize() == high
    assert handoff.peak == high
    # Still queued: a second produce is not a second row.
    assert handoff.put(first[0], {"url": first[0]}) is True
    assert handoff.qsize() == high

    extra = [f"https://uconn.edu/s{i}" for i in range(high, high + 8)]
    assert handoff.put(extra[0], {"url": extra[0]}) is False
    held.append(extra[0])
    assert held == [extra[0]]
    assert sleeps
    for url in extra[1:]:
        assert handoff.put(url, {"url": url}) is False
        assert handoff.qsize() == high
        assert handoff.peak == high
    assert len(held) == 1

    while True:
        item = handoff.get()
        if item is None:
            break
        delivered.append(item["url"])
    assert delivered == first
    assert handoff.qsize() == 0

    monkeypatch.setenv("REDIS_QUEUE_BACKPRESSURE_MAX_BLOCK_SECONDS", "30")
    drain["on"] = True
    for url in extra:
        assert handoff.put(url, {"url": url}) is True
        assert handoff.qsize() <= high
    while True:
        item = handoff.get()
        if item is None:
            break
        delivered.append(item["url"])

    assert delivered == first + extra
    assert len(delivered) == len(set(delivered))
    assert handoff.peak <= high
    assert handoff.qsize() == 0
