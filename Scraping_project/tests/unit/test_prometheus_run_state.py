"""Crawl-run metrics state is per run and thread-safe (#28).

The Prometheus extension kept crawl start times and skip tallies in
module-global dicts keyed by spider *name*. Two concurrent runs of the same
spider in one process clobbered each other, and tally increments were
unlocked read-modify-write operations.
"""
import threading
from types import SimpleNamespace

import pytest

from src import scrapy_prometheus as sp
from src.scrapy_prometheus import CrawlRunState


def spider(name="scout"):
    return SimpleNamespace(name=name)


def test_module_globals_are_gone():
    assert not hasattr(sp, "CRAWL_START_TIMES")
    assert not hasattr(sp, "SKIPPED_URL_TALLIES")


def test_two_same_name_runs_do_not_clobber_each_other():
    state = CrawlRunState()
    a, b = spider(), spider()  # same name, different runs
    state.open(a, now=100.0)
    state.open(b, now=200.0)
    state.tally(a, "offsite")
    state.tally(b, "duplicate")
    state.tally(b, "duplicate")

    started_a, tallies_a = state.close(a)
    assert started_a == 100.0 and tallies_a == {"offsite": 1}
    # Closing run A must leave run B intact (the old code deleted B's state).
    assert state.active_runs() == 1
    started_b, tallies_b = state.close(b)
    assert started_b == 200.0 and tallies_b == {"duplicate": 2}


def test_concurrent_tallies_lose_no_updates():
    state = CrawlRunState()
    s = spider()
    state.open(s)
    barrier = threading.Barrier(8)

    def work(i):
        barrier.wait()
        for _ in range(2000):
            state.tally(s, f"r{i % 2}")

    ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    _, tallies = state.close(s)
    assert tallies == {"r0": 8000, "r1": 8000}


def test_tally_returns_snapshots_not_live_dicts():
    state = CrawlRunState()
    s = spider()
    total, snap = state.tally(s, "x")
    snap["x"] = 999
    assert state.tally(s, "x") == (2, {"x": 2})


def test_close_of_unknown_run_is_harmless():
    assert CrawlRunState().close(spider()) == (None, {})


@pytest.mark.skipif(not sp.PROMETHEUS_AVAILABLE, reason="prometheus_client missing")
def test_extension_end_to_end_with_overlapping_same_name_runs(monkeypatch):
    from prometheus_client import REGISTRY

    ext = sp.PrometheusExtension(port=0, host="127.0.0.1")
    monkeypatch.setattr(ext, "start_server", lambda: None)
    clock = iter([10.0, 20.0, 25.0, 50.0])
    import time as _time
    # Replace only this module's view of `time`; patching time.time globally breaks Twisted.
    monkeypatch.setattr(sp, "time", SimpleNamespace(time=lambda: next(clock), strftime=_time.strftime))
    name = "run-state-e2e"
    a, b = spider(name), spider(name)
    ext.spider_opened(a)      # t=10
    ext.spider_opened(b)      # t=20
    ext.item_scraped({"skip_reason": "too_old"}, a)
    ext.spider_closed(a, "finished")  # t=25 -> A ran 15s
    assert REGISTRY.get_sample_value("scrapy_crawl_duration_seconds", {"spider": name}) == 15.0
    assert ext.runs.active_runs() == 1, "closing A dropped B's run"
    ext.spider_closed(b, "finished")  # t=50 -> B ran 30s from its own start
    assert REGISTRY.get_sample_value("scrapy_crawl_duration_seconds", {"spider": name}) == 30.0
    assert ext.runs.active_runs() == 0
