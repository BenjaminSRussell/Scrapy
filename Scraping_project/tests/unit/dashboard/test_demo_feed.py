"""DemoFeed: a seeded, realistic simulated crawl (spec: Demo feed)."""
from __future__ import annotations

import pytest

from ccfeed.demo import DEMO_HOSTS, DemoFeed
from ccfeed.stages import STAGES

T = 1_790_000_000.0


class Clock:
    def __init__(self, t=T):
        self.t = t

    def __call__(self):
        return self.t


def run(feed, clock, seconds, step=1.0):
    """Poll every ``step`` seconds of simulated time and collect live (non-replay) events."""
    events = []
    end = clock.t + seconds
    while clock.t < end:
        clock.t += step
        events += [e for e in feed.poll(clock.t).events if not e.get("replay")]
    return events


def test_same_seed_gives_the_same_crawl():
    def first_50(seed):
        clock = Clock()
        feed = DemoFeed(seed=seed, clock=clock)
        feed.poll(clock.t)
        return [(e["kind"], e["url"]) for e in run(feed, clock, 120)][:50]

    assert first_50(7) == first_50(7)
    assert first_50(7) != first_50(8)


def test_first_poll_replays_warm_up_history_without_animating_it():
    clock = Clock()
    res = DemoFeed(seed=1, clock=clock).poll(clock.t)
    assert 0 < len(res.events) <= 50
    assert all(e["replay"] for e in res.events)
    assert all(res.stages[s.id].status == "ok" and res.stages[s.id].count for s in STAGES)


def test_rates_after_warm_up_are_in_range():
    clock = Clock()
    feed = DemoFeed(seed=3, clock=clock)
    feed.poll(clock.t)
    events = run(feed, clock, 600)
    per_min = lambda kind: sum(1 for e in events if e["kind"] == kind) / 10  # noqa: E731
    assert 40 <= per_min("discovered") <= 120
    assert 15 <= per_min("analyzed") <= 50
    assert 5 <= per_min("summarized") <= 40
    assert per_min("error") < per_min("discovered") * 0.06


def test_depth_is_parent_depth_plus_one_and_routing_is_plausible():
    clock = Clock()
    feed = DemoFeed(seed=5, clock=clock)
    feed.poll(clock.t)
    run(feed, clock, 300)
    found = feed._rows["discovery"]
    by_url = {r["url"]: r for r in found}
    with_parent = [r for r in found if r.get("parent_url") in by_url]
    assert len(with_parent) > 100
    assert all(r["depth"] == by_url[r["parent_url"]]["depth"] + 1 for r in with_parent)
    analyzed = feed._rows["analysis"]
    massive = sum(1 for r in analyzed if r["is_massive_doc"])
    assert 0.02 <= massive / len(analyzed) <= 0.10


def test_journeys_follow_the_skip_rules():
    clock = Clock()
    feed = DemoFeed(seed=11, clock=clock)
    feed.poll(clock.t)
    run(feed, clock, 300)
    big = next(r["url"] for r in feed._rows["large_docs"])
    steps = {s["stage"]: s for s in feed.url_journey(big)["steps"]}
    assert steps["large_docs"]["status"] == "done"
    assert steps["summaries"]["note"] == "Routed to Large Docs"
    summed = next(r["url"] for r in feed._rows["summaries"])
    steps = {s["stage"]: s for s in feed.url_journey(summed)["steps"]}
    assert steps["summaries"]["status"] == "done" and steps["large_docs"]["status"] == "skipped"
    assert feed.url_journey("https://nowhere.example/") is None


def test_detail_explore_services_and_backfill():
    clock = Clock()
    feed = DemoFeed(seed=2, clock=clock)
    series = feed.backfill(clock.t, 1800, 10)
    assert set(series) == {s.id for s in STAGES}
    disc = [c for _, c in series["discovery"]]
    assert len(disc) == 180 and disc == sorted(disc) and disc[-1] > disc[0] + 1000
    res = feed.poll(clock.t)
    assert res.stages["discovery"].count == disc[-1]
    detail = feed.stage_detail("discovery")
    assert {b["id"] for b in detail["breakdowns"]} >= {"depth", "status", "summary"}
    assert len(detail["recent"]) == 50
    assert feed.stage_detail("nope") is None
    root = feed.explore([])
    assert {c["segment"] for c in root["children"]} <= set(DEMO_HOSTS)
    assert len(root["children"]) >= 5
    assert feed.explore([root["children"][0]["segment"]])["total"] > 0
    assert feed.explore(["nope"]) is None
    services = feed.services(clock.t)
    assert {s["id"] for s in services} == {"redis", "delta", "metrics"}
    assert all(s["status"] == "ok" and "Simulated" in s["detail"] for s in services)
    queue = services[0]["data"]["queues"][0]
    assert queue["key"] == "js_spider:priority_queue" and queue["depth"] >= 0


@pytest.mark.parametrize("seconds", [0, 3600 * 6])
def test_memory_stays_bounded_over_long_runs(seconds):
    clock = Clock()
    feed = DemoFeed(seed=4, clock=clock)
    feed.poll(clock.t)
    clock.t += seconds
    feed.poll(clock.t)  # one big catch-up step, as after a laptop sleep
    assert sum(len(v) for v in feed._rows.values()) <= 4 * 60_000


def test_long_runs_stay_bounded_and_never_raise():
    """Eviction must keep queues and indexes consistent (it used to KeyError after ~18 h)."""
    clock = Clock()
    feed = DemoFeed(seed=4, clock=clock, max_urls=1500, evict=400, max_backlog=300)
    feed.poll(clock.t)
    for _ in range(24):  # 12 simulated hours, half an hour per poll
        clock.t += 1800
        feed.poll(clock.t)  # raises if eviction left a dangling URL
    assert len(feed._rows["discovery"]) <= 1500
    assert all(len(rows) <= 1500 for rows in feed._rows.values())
    assert len(feed._by_url) <= 1500 + 300
    assert all(len(q) <= 300 for q in feed._queues.values())
    live = set(feed._by_url)
    assert all(url in live for q in feed._queues.values() for url in q)
