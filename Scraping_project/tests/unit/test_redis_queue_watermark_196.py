"""#196: Redis queue high/low watermark backpressure.

Queue depth is passed in (or read from fakeredis). No Redis server is started.
"""

import pytest

from src.utils import redis as r
from src.utils.redis import admit_enqueue, queue_watermark_settings, reset_queue_backpressure

pytestmark = pytest.mark.unit

_ENV = (
    r.HIGH_WATERMARK_ENV,
    r.LOW_WATERMARK_ENV,
    r.BACKPRESSURE_MODE_ENV,
    r.BACKPRESSURE_SLEEP_ENV,
    r.BACKPRESSURE_MAX_BLOCK_ENV,
    r.SHED_BELOW_PRIORITY_ENV,
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    reset_queue_backpressure()
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    yield
    reset_queue_backpressure()


def _events(queue: str, action: str) -> float:
    if r.REDIS_QUEUE_BACKPRESSURE is None:
        return 0.0
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(
        "redis_queue_backpressure_total", {"queue": queue, "action": action}
    ) or 0.0


def _clock():
    now = {"t": 0.0}
    sleeps = []

    def sleeper(seconds):
        sleeps.append(seconds)
        now["t"] += seconds

    return sleeps, sleeper, lambda: now["t"]


def test_defaults_match_the_queue_backlog_alert():
    cfg = queue_watermark_settings()
    assert cfg["high"] == 10_000
    assert cfg["low"] == 8_000
    assert cfg["low"] < cfg["high"]
    assert cfg["mode"] == "block"
    assert cfg["shed_below"] == 50


def test_env_overrides_and_invalid_values_fall_back(monkeypatch):
    monkeypatch.setenv(r.HIGH_WATERMARK_ENV, "40")
    monkeypatch.setenv(r.LOW_WATERMARK_ENV, "0")
    monkeypatch.setenv(r.BACKPRESSURE_MODE_ENV, "shed")
    monkeypatch.setenv(r.SHED_BELOW_PRIORITY_ENV, "25")
    cfg = queue_watermark_settings()
    assert (cfg["high"], cfg["low"], cfg["mode"], cfg["shed_below"]) == (40, 0, "shed", 25)

    monkeypatch.setenv(r.HIGH_WATERMARK_ENV, "nope")
    monkeypatch.setenv(r.LOW_WATERMARK_ENV, "-3")
    monkeypatch.setenv(r.BACKPRESSURE_MODE_ENV, "pause")
    monkeypatch.setenv(r.BACKPRESSURE_SLEEP_ENV, "0")
    cfg = queue_watermark_settings()
    assert cfg["high"] == r.DEFAULT_QUEUE_HIGH_WATERMARK
    assert cfg["low"] == r.DEFAULT_QUEUE_LOW_WATERMARK
    assert cfg["mode"] == "block"
    assert cfg["sleep"] == r.DEFAULT_BACKPRESSURE_SLEEP


def test_low_watermark_is_clamped_below_high(monkeypatch):
    monkeypatch.setenv(r.HIGH_WATERMARK_ENV, "100")
    monkeypatch.setenv(r.LOW_WATERMARK_ENV, "100")
    assert queue_watermark_settings()["low"] == 80
    monkeypatch.setenv(r.HIGH_WATERMARK_ENV, "1")
    monkeypatch.setenv(r.LOW_WATERMARK_ENV, "5")
    assert queue_watermark_settings()["low"] == 0


def test_settings_expose_the_same_resolver():
    from src import settings as project_settings

    assert project_settings.redis_queue_watermarks is queue_watermark_settings
    assert project_settings.REDIS_QUEUE_LOW_WATERMARK < project_settings.REDIS_QUEUE_HIGH_WATERMARK
    assert project_settings.REDIS_QUEUE_BACKPRESSURE_MODE in ("block", "shed")
    # Live read, not the import-time snapshot.
    assert project_settings.redis_queue_watermarks()["high"] == r.DEFAULT_QUEUE_HIGH_WATERMARK


def test_depth_at_or_under_the_high_watermark_is_admitted():
    before = _events("under", "shed") + _events("under", "block")
    assert admit_enqueue("under", 10_000, priority=1) is True
    assert admit_enqueue("under", 0, priority=1) is True
    assert _events("under", "shed") + _events("under", "block") == before


def test_shed_mode_refuses_low_priority_and_holds_until_the_low_watermark(monkeypatch):
    monkeypatch.setenv(r.HIGH_WATERMARK_ENV, "10")
    monkeypatch.setenv(r.LOW_WATERMARK_ENV, "4")
    monkeypatch.setenv(r.BACKPRESSURE_MODE_ENV, "shed")
    sleeps, sleeper, _clock_fn = _clock()
    shed = _events("hq", "shed")
    assert admit_enqueue("hq", 11, priority=1, sleeper=sleeper) is False
    assert admit_enqueue("hq", 6, priority=None, sleeper=sleeper) is False  # hysteresis
    assert sleeps == []
    assert _events("hq", "shed") == shed + 2
    assert admit_enqueue("hq", 4, priority=1) is True
    assert admit_enqueue("hq", 6, priority=1) is True  # between the marks, gate open
    assert admit_enqueue("hq", 11, priority=1) is False


def test_shed_mode_blocks_high_priority_until_the_queue_drains(monkeypatch):
    monkeypatch.setenv(r.HIGH_WATERMARK_ENV, "10")
    monkeypatch.setenv(r.LOW_WATERMARK_ENV, "4")
    monkeypatch.setenv(r.BACKPRESSURE_MODE_ENV, "shed")
    monkeypatch.setenv(r.SHED_BELOW_PRIORITY_ENV, "50")
    monkeypatch.setenv(r.BACKPRESSURE_SLEEP_ENV, "0.5")
    monkeypatch.setenv(r.BACKPRESSURE_MAX_BLOCK_ENV, "5")
    sleeps, sleeper, clock = _clock()
    seen = {"n": 0}

    def probe():
        seen["n"] += 1
        return 20 if seen["n"] < 3 else 4

    blocked = _events("hipri", "block")
    assert admit_enqueue(
        "hipri", 20, priority=80, depth_probe=probe, sleeper=sleeper, clock=clock
    ) is True
    assert sleeps == [0.5, 0.5]
    assert _events("hipri", "block") == blocked + 1


def test_block_mode_gives_up_after_the_budget_and_does_not_sleep_again(monkeypatch):
    monkeypatch.setenv(r.HIGH_WATERMARK_ENV, "10")
    monkeypatch.setenv(r.LOW_WATERMARK_ENV, "4")
    monkeypatch.setenv(r.BACKPRESSURE_MODE_ENV, "block")
    monkeypatch.setenv(r.BACKPRESSURE_SLEEP_ENV, "0.5")
    monkeypatch.setenv(r.BACKPRESSURE_MAX_BLOCK_ENV, "1")
    sleeps, sleeper, clock = _clock()
    assert admit_enqueue(
        "stuck", 50, priority=1, depth_probe=lambda: 50, sleeper=sleeper, clock=clock
    ) is False
    assert sleeps == [0.5, 0.5]
    assert _events("stuck", "block") >= 1
    shed = _events("stuck", "shed")
    again = []
    assert admit_enqueue(
        "stuck", 50, priority=1, depth_probe=lambda: 50, sleeper=lambda s: again.append(s)
    ) is False
    assert again == []
    assert _events("stuck", "shed") == shed + 1
    # Drained to the low watermark: the next push is admitted and the gate reopens.
    assert admit_enqueue("stuck", 4, priority=1) is True
    assert admit_enqueue("stuck", 8, priority=1) is True


def test_js_producer_sheds_when_mocked_depth_exceeds_the_high_watermark(monkeypatch):
    fakeredis = pytest.importorskip("fakeredis")
    from src.stage1.processors.js_priority_queue import JSPriorityQueue

    monkeypatch.setenv(r.HIGH_WATERMARK_ENV, "2")
    monkeypatch.setenv(r.LOW_WATERMARK_ENV, "1")
    monkeypatch.setenv(r.BACKPRESSURE_MODE_ENV, "shed")
    q = JSPriorityQueue(
        fakeredis.FakeRedis(decode_responses=True),
        queue_key="wm196",
        max_size=0,
        ttl_seconds=0,
    )
    assert q.enqueue("https://u.edu/a", priority=1) is True
    assert q.enqueue("https://u.edu/b", priority=1) is True
    assert q.enqueue("https://u.edu/c", priority=10) is True  # depth was 2, not over
    shed = _events("wm196", "shed")
    assert q.enqueue("https://u.edu/d", priority=10) is False
    assert _events("wm196", "shed") == shed + 1
    assert q.size() == 3
    assert not q.redis.sismember(q.hash_key, "https://u.edu/d")
    q.redis.zrem(q.queue_key, "https://u.edu/a")
    assert q.size() == 2
    assert q.enqueue("https://u.edu/e", priority=1) is False  # still above the low watermark
    q.redis.zrem(q.queue_key, "https://u.edu/b")
    assert q.size() == 1  # c remains; depth is at the low watermark
    assert q.enqueue("https://u.edu/f", priority=1) is True

    batch = JSPriorityQueue(
        fakeredis.FakeRedis(decode_responses=True),
        queue_key="wm196b",
        max_size=0,
        ttl_seconds=0,
    )
    accepted = batch.enqueue_batch([(f"https://u.edu/n{i}", 1, None) for i in range(5)])
    assert accepted == 3  # depth 0, 1, then 2 is not over; the 4th sees depth 3
    assert batch.size() == 3
    assert _events("wm196b", "shed") == 2
