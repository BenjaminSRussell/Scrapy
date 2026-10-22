"""Scout seed rows are batched and merged off the reactor thread (#1644)."""

from __future__ import annotations

import pytest
from twisted.internet import defer

from src.stage1 import scout_spider as scout_mod
from src.stage1.scout_spider import ScoutSpider


class _FakeSeeds:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls: list[list[tuple[str, str]]] = []

    def add_seed_pairs(self, pairs, source_spider, *, write_domain_urls=None, enqueue_stage2=False):
        self.calls.append(list(pairs))
        if self.error is not None:
            raise self.error
        return {"seed_inserted": len(pairs), "domain_inserted": 0, "uconn_inserted": 0, "stage2_enqueued": 0}


def _spider(seeds: _FakeSeeds, *, max_rows: int = 500, max_age: float = 30.0) -> ScoutSpider:
    spider = ScoutSpider.__new__(ScoutSpider)  # skip I/O-heavy __init__
    spider.name = "scout"
    spider.seed_manager = seeds
    spider.seed_batch_max_rows = max_rows
    spider.seed_batch_max_age = max_age
    spider._seed_pending = []
    spider._seed_pending_since = None
    spider._seed_flush_in_flight = None
    spider._seed_timer = None
    return spider


@pytest.fixture
def no_timer(monkeypatch):
    monkeypatch.setattr(ScoutSpider, "_start_seed_timer", lambda self: None)


@pytest.fixture
def inline_threads(monkeypatch, no_timer):
    """Run the 'thread' work inline and return a Deferred that has already fired."""

    def fake(fn, *args, **kwargs):
        return defer.succeed(fn(*args, **kwargs))

    monkeypatch.setattr(scout_mod.threads, "deferToThread", fake)


@pytest.fixture
def held_threads(monkeypatch, no_timer):
    """Hand back Deferreds the test fires by hand, so a merge can stay in flight."""
    held: list[defer.Deferred] = []

    def fake(fn, *args, **kwargs):
        d = defer.Deferred()
        held.append(d)
        return d

    monkeypatch.setattr(scout_mod.threads, "deferToThread", fake)
    return held


def test_queue_does_no_merge_below_threshold(inline_threads):
    seeds = _FakeSeeds()
    spider = _spider(seeds)

    spider._queue_seed_urls(["https://e.org/a", "https://e.org/b"], "https://e.org/")

    assert seeds.calls == []
    assert len(spider._seed_pending) == 2


def test_one_merge_per_full_batch_and_one_for_the_tail(inline_threads):
    seeds = _FakeSeeds()
    spider = _spider(seeds, max_rows=500)

    for i in range(1200):  # one URL per parsed page
        spider._queue_seed_urls([f"https://e.org/{i}"], f"https://e.org/p{i}")
    spider._drain_seed_batches()

    assert [len(batch) for batch in seeds.calls] == [500, 500, 200]


def test_rows_keep_their_own_source_url(inline_threads):
    seeds = _FakeSeeds()
    spider = _spider(seeds, max_rows=2)

    spider._queue_seed_urls(["https://e.org/a"], "https://e.org/p1")
    spider._queue_seed_urls(["https://e.org/b"], "https://e.org/p2")

    assert seeds.calls == [[("https://e.org/a", "https://e.org/p1"), ("https://e.org/b", "https://e.org/p2")]]


def test_age_makes_a_batch_due_without_new_pages(no_timer):
    spider = _spider(_FakeSeeds(), max_age=30.0)
    spider._seed_pending = [("https://e.org/a", "https://e.org/")]

    spider._seed_pending_since = scout_mod.time.monotonic()
    assert spider._seed_batch_due() is False

    spider._seed_pending_since = scout_mod.time.monotonic() - 31.0
    assert spider._seed_batch_due() is True


def test_only_one_merge_in_flight_and_rows_keep_buffering(held_threads):
    seeds = _FakeSeeds()
    spider = _spider(seeds, max_rows=500)

    for i in range(600):
        spider._queue_seed_urls([f"https://e.org/{i}"], "https://e.org/")
    assert len(held_threads) == 1  # first 500 rows are in flight

    for i in range(600, 1200):
        spider._queue_seed_urls([f"https://e.org/{i}"], "https://e.org/")
    assert len(held_threads) == 1  # nothing new starts while that merge runs
    assert len(spider._seed_pending) == 700

    held_threads[0].callback(None)  # first merge lands; the buffered rows go next
    assert len(held_threads) == 2
    assert len(spider._seed_pending) == 0


def test_drain_waits_for_the_in_flight_merge_and_writes_the_tail(held_threads):
    seeds = _FakeSeeds()
    spider = _spider(seeds, max_rows=500)
    for i in range(500):
        spider._queue_seed_urls([f"https://e.org/{i}"], "https://e.org/")
    spider._queue_seed_urls(["https://e.org/tail"], "https://e.org/")

    finished: list[bool] = []
    done = spider._drain_seed_batches()
    assert done is not None
    done.addCallback(lambda _: finished.append(True))
    assert finished == []

    held_threads[0].callback(None)  # the 500-row merge lands
    assert len(held_threads) == 2  # then the tail is written
    assert finished == []
    held_threads[1].callback(None)
    assert finished == [True]


def test_drain_with_nothing_buffered_returns_none(inline_threads):
    spider = _spider(_FakeSeeds())
    assert spider._drain_seed_batches() is None


def test_merge_failure_is_logged_not_raised(inline_threads, caplog):
    spider = _spider(_FakeSeeds(error=RuntimeError("lake down")))

    spider._merge_seed_batch([("https://e.org/a", "https://e.org/")])

    assert "Failed to add URLs via SeedManager" in caplog.text
