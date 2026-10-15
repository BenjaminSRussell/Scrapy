"""Stages helpers, rate history and the Hub (spec: Hub, Services)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ccfeed.history import History
from ccfeed.hub import Hub
from ccfeed.stages import STAGES, PollResult, StageState, freshness, parse_ts

T0 = 1_760_000_000.0


# ---- stages helpers ----

@pytest.mark.parametrize("raw", [
    "2026-09-21T13:47:50.281369",
    "2026-09-21T13:47:50.281369+00:00",
    "2026-09-21T13:47:50.281369Z",
    datetime(2026, 9, 21, 13, 47, 50, 281369),
    datetime(2026, 9, 21, 13, 47, 50, 281369, tzinfo=timezone.utc),
])
def test_parse_ts_reads_every_on_disk_form_as_utc(raw):
    expected = datetime(2026, 9, 21, 13, 47, 50, 281369, tzinfo=timezone.utc).timestamp()
    assert parse_ts(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [None, "", "not a time", 12, "2026-13-40"])
def test_parse_ts_rejects_junk(raw):
    assert parse_ts(raw) is None


@pytest.mark.parametrize("age, label", [(0, "active"), (119, "active"), (120, "idle"),
                                         (1799, "idle"), (1800, "stale"), (86_400, "stale")])
def test_freshness_thresholds(age, label):
    assert freshness(T0 - age, T0) == label


def test_freshness_without_a_write_is_none():
    assert freshness(None, T0) == "none"


def test_stage_ids_follow_the_pipeline():
    assert [s.id for s in STAGES] == ["seeds", "discovery", "analysis", "summaries", "large_docs"]


# ---- history ----

def test_rate_uses_real_elapsed_time():
    h = History()
    h.add(T0, 0)
    h.add(T0 + 30, 60)
    assert h.rate_per_min(T0 + 30) == pytest.approx(120.0)


def test_rate_needs_two_samples():
    h = History()
    h.add(T0, 5)
    assert h.rate_per_min(T0) is None


def test_count_drop_clamps_rate_to_zero():
    h = History()
    h.add(T0, 100)
    h.add(T0 + 10, 40)  # vacuum or overwrite shrank the table
    assert h.rate_per_min(T0 + 10) == 0.0


def test_series_is_capped_to_180_buckets_of_rates():
    h = History(bucket=10, keep=180)
    for i in range(400):
        h.add(T0 + i * 10, i * 5)  # 5 per 10 s = 30/min
    series = h.series()
    assert len(series) == 180
    assert all(len(p) == 2 for p in series)
    assert series[-1][1] == pytest.approx(30.0)


def test_seed_fills_series_from_backfill():
    h = History(bucket=10)
    h.seed([(T0 + i * 10, i * 2) for i in range(1, 31)])
    assert len(h.series()) >= 29
    assert h.series()[-1][1] == pytest.approx(12.0)


# ---- hub ----

class FakeFeed:
    mode = "demo"

    def __init__(self):
        self.count = 0
        self.seq = 0
        self.fail = False
        self.backfilled = False

    def poll(self, now):
        if self.fail:
            raise RuntimeError("lake on fire")
        self.count += 3
        events = []
        for _ in range(3):
            self.seq += 1
            events.append({"id": f"discovery:{self.seq}", "stage": "discovery", "kind": "discovered",
                           "url": f"https://example.edu/p{self.seq}", "t": now, "fields": {}})
        stages = {s.id: StageState() for s in STAGES}
        stages["discovery"] = StageState(status="ok", count=self.count, last_write=now, version=self.seq)
        return PollResult(stages=stages, events=events)

    def backfill(self, now, seconds, bucket):
        self.backfilled = True
        return {"discovery": [(now - seconds + i * bucket, i) for i in range(seconds // bucket)]}

    def services(self, now):
        return [{"id": "delta", "label": "Delta Lake", "status": "ok", "detail": "fake",
                 "checked_at": now, "data": {}}]

    def stage_detail(self, stage_id):
        return None

    def url_journey(self, url):
        return None

    def explore(self, path):
        return None


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def make_hub(**kw):
    clock = Clock()
    feed = FakeFeed()
    return Hub(feed, interval=2.0, clock=clock, **kw), feed, clock


def test_snapshot_reports_stage_rates_freshness_and_recent():
    hub, feed, clock = make_hub()
    for _ in range(40):
        hub.step()
        clock.t += 2
    snap = hub.snapshot()
    assert snap["mode"] == "demo"
    assert [s["id"] for s in snap["stages"]] == ["seeds", "discovery", "analysis", "summaries", "large_docs"]
    disc = next(s for s in snap["stages"] if s["id"] == "discovery")
    assert disc["count"] == 120
    assert disc["rate_per_min"] == pytest.approx(90.0)  # 3 per 2 s
    assert disc["freshness"] == "active"
    seeds = snap["stages"][0]
    assert seeds["status"] == "missing" and seeds["count"] is None and seeds["freshness"] == "none"
    assert len(snap["recent"]) == 50
    assert snap["recent"][0]["id"] == "discovery:120"  # newest first
    assert {"from": "analysis", "to": "large_docs"} in snap["edges"]
    assert snap["services"][0]["id"] == "delta"


def test_backfill_runs_once_and_seeds_series():
    hub, feed, clock = make_hub()
    hub.step()
    assert feed.backfilled
    assert len(hub.snapshot()["series"]["discovery"]) > 100


def test_subscriber_gets_snapshot_then_ticks_with_new_events():
    hub, feed, clock = make_hub()
    hub.step()
    sub = hub.subscribe()
    kind, first = sub.get(timeout=0.1)
    assert kind == "snapshot" and first["mode"] == "demo"
    clock.t += 2
    hub.step()
    kind, tick = sub.get(timeout=0.1)
    assert kind == "tick"
    assert [e["id"] for e in tick["events"]] == ["discovery:4", "discovery:5", "discovery:6"]
    assert "discovery" in tick["series_point"]
    assert hub.subscriber_count == 1
    hub.unsubscribe(sub)
    assert hub.subscriber_count == 0


def test_slow_subscriber_is_resynced_with_a_snapshot():
    hub, feed, clock = make_hub()
    hub.step()
    sub = hub.subscribe()
    for _ in range(100):  # never read: the bounded queue overflows
        clock.t += 2
        hub.step()
    kind, payload = sub.get(timeout=0.1)
    assert kind == "snapshot"  # the backlog was replaced, not delivered half-dropped
    last = payload
    while (item := sub.get(timeout=0.01)) is not None:
        assert item[0] == "tick"
        last = item[1]
    assert last["stages"][1]["count"] == 303


def test_failing_poll_keeps_hub_alive_and_reports_it():
    hub, feed, clock = make_hub()
    hub.step()
    feed.fail = True
    clock.t += 2
    hub.step()
    snap = hub.snapshot()
    assert snap["poll_error"] == "RuntimeError: lake on fire"
    assert snap["stages"][1]["count"] == 3  # last known state kept
    feed.fail = False
    clock.t += 2
    hub.step()
    assert hub.snapshot()["poll_error"] is None


def test_probe_rows_join_feed_services_and_probe_errors_are_contained():
    def probe():
        raise ConnectionError("nope")

    hub, feed, clock = make_hub(probe=probe)
    hub.step()
    ids = [s["id"] for s in hub.snapshot()["services"]]
    assert ids[0] == "delta"
    probe_row = hub.snapshot()["services"][-1]
    assert probe_row["status"] == "unknown" and "ConnectionError" in probe_row["detail"]


class SlowFeed(FakeFeed):
    def poll(self, now):
        import time as _time

        _time.sleep(0.3)  # a background-only first poll would still be sleeping below
        return super().poll(now)


def test_start_polls_once_before_returning():
    hub = Hub(SlowFeed(), interval=60, clock=Clock())
    hub.start()
    try:
        assert hub.snapshot()["stages"][1]["count"] == 3  # no request ever sees an unpolled hub
    finally:
        hub.stop()


def test_start_and_stop_run_the_background_loop():
    import time as _time

    feed = FakeFeed()
    hub = Hub(feed, interval=0.05)
    hub.start()
    try:
        deadline = _time.time() + 2
        while feed.count < 6 and _time.time() < deadline:
            _time.sleep(0.02)
    finally:
        hub.stop()
    assert feed.count >= 6
