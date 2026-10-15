"""Stage detail, URL journey and explore builders, and LiveFeed over a temp lake."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pyarrow as pa
import pytest

from ccfeed.queries import ExploreTree, build_journey, build_stage_detail

T = 1_790_000_000.0


def bars(detail, bid):
    b = next(b for b in detail["breakdowns"] if b["id"] == bid)
    assert b["kind"] == "bars"
    return [(i["label"], i["value"]) for i in b["items"]]


def stats(detail, bid):
    b = next(b for b in detail["breakdowns"] if b["id"] == bid)
    assert b["kind"] == "stats"
    return {i["label"]: (i["value"], i["format"]) for i in b["items"]}


# ---- stage detail ----

def test_discovery_detail_has_depth_status_and_js_share():
    rows = [
        {"url": "https://a.edu/", "domain": "a.edu", "depth": 0, "status_code": 200, "requires_js": True, "content_size": 1000},
        {"url": "https://a.edu/x", "domain": "a.edu", "depth": 1, "status_code": 200, "requires_js": False, "content_size": 3000},
        {"url": "https://b.edu/y", "domain": "b.edu", "depth": 1, "status_code": 404, "requires_js": False, "content_size": None},
        {"url": "https://b.edu/z", "domain": "b.edu", "depth": 2, "status_code": 301, "requires_js": False, "content_size": 2000},
    ]
    recent = [{"url": "https://b.edu/z", "_t": T, "depth": 2, "status_code": 301}]
    d = build_stage_detail("discovery", rows, recent)
    assert bars(d, "depth") == [("0", 1), ("1", 2), ("2", 1)]
    assert bars(d, "status")[0] == ("200", 2)
    s = stats(d, "summary")
    assert s["Needs JavaScript"] == (25.0, "percent")
    assert s["Mean page size"] == (2000.0, "bytes")
    assert s["Hosts"] == (2, "int")
    assert d["recent"] == [{"url": "https://b.edu/z", "t": T, "fields": {"depth": 2, "status_code": 301}}]


def test_analysis_detail_splits_routing_and_averages_words():
    rows = [
        {"is_massive_doc": True, "word_count": 12000, "has_quality_content": True},
        {"is_massive_doc": False, "word_count": 400, "has_quality_content": True},
        {"is_massive_doc": False, "word_count": 800, "has_quality_content": False},
        {"is_massive_doc": None, "word_count": None, "has_quality_content": None},
    ]
    d = build_stage_detail("analysis", rows, [])
    assert bars(d, "routing") == [("Summaries", 2), ("Large Docs", 1), ("Not routed", 1)]
    s = stats(d, "summary")
    assert s["Mean word count"] == (4400.0, "int")
    assert s["Quality content"] == (pytest.approx(200 / 3), "percent")


def test_summaries_and_large_docs_details():
    d = build_stage_detail("summaries", [{"model": "bart"}, {"model": "bart"}, {"model": "t5"}], [])
    assert bars(d, "models") == [("bart", 2), ("t5", 1)]
    d = build_stage_detail("large_docs", [{"compression_ratio": 0.01, "original_word_count": 60000},
                                          {"compression_ratio": 0.03, "original_word_count": 20000}], [])
    s = stats(d, "summary")
    assert s["Mean compression"] == (pytest.approx(0.02), "ratio")
    assert s["Mean original words"] == (40000.0, "int")


def test_detail_leaves_out_breakdowns_it_has_no_data_for():
    d = build_stage_detail("discovery", [{"url": "https://a.edu/"}], [])
    assert [b["id"] for b in d["breakdowns"]] == ["summary"]
    assert stats(d, "summary") == {"Hosts": (1, "int")}


def test_seeds_detail_counts_hosts_from_urls():
    d = build_stage_detail("seeds", [{"url": "https://a.edu/"}, {"url": "https://a.edu/b"}, {"url": "https://c.edu/"}], [])
    assert bars(d, "hosts") == [("a.edu", 2), ("c.edu", 1)]


# ---- journey ----

def step(j, sid):
    return next(s for s in j["steps"] if s["stage"] == sid)


def test_journey_routed_to_summaries():
    url = "https://a.edu/news/item?id=1"
    j = build_journey(url, {
        "seeds": None,
        "discovery": {"_t": T, "depth": 2, "status_code": 200, "content_size": 5120, "requires_js": False},
        "analysis": {"_t": T + 5, "title": "Item", "word_count": 640, "is_massive_doc": False},
        "summaries": {"_t": T + 9, "model": "bart", "summary": "A short summary."},
        "large_docs": None,
    })
    assert (j["host"], j["path"]) == ("a.edu", "/news/item?id=1")
    assert [s["stage"] for s in j["steps"]] == ["seeds", "discovery", "analysis", "summaries", "large_docs"]
    assert (step(j, "seeds")["status"], step(j, "seeds")["note"]) == ("skipped", "Not a seed")
    assert step(j, "discovery")["status"] == "done" and step(j, "discovery")["t"] == T
    assert {"label": "Depth", "value": 2, "format": "int"} in step(j, "discovery")["fields"]
    assert step(j, "summaries")["status"] == "done"
    assert (step(j, "large_docs")["status"], step(j, "large_docs")["note"]) == ("skipped", "Routed to Summaries")
    assert j["parent_url"] is None


def test_journey_routed_to_large_docs_waits_for_it():
    j = build_journey("https://a.edu/big.pdf", {
        "seeds": {"_t": T - 60, "priority": 1},
        "discovery": {"_t": T, "parent_url": "https://a.edu/"},
        "analysis": {"_t": T + 5, "is_massive_doc": True},
        "summaries": None, "large_docs": None,
    })
    assert step(j, "seeds")["status"] == "done"
    assert (step(j, "summaries")["status"], step(j, "summaries")["note"]) == ("skipped", "Routed to Large Docs")
    assert step(j, "large_docs")["status"] == "waiting"
    assert j["parent_url"] == "https://a.edu/"


def test_journey_before_analysis_waits_on_both_branches():
    j = build_journey("https://a.edu/x", {"seeds": None, "discovery": {"_t": T}, "analysis": None,
                                          "summaries": None, "large_docs": None})
    assert [step(j, s)["status"] for s in ("analysis", "summaries", "large_docs")] == ["waiting"] * 3


# ---- explore ----

def urls():
    out = [("https://a.edu/", 0), ("https://a.edu/news/", 1), ("https://a.edu/news/one", 2),
           ("https://a.edu/news/two", 2), ("https://a.edu/about", 1), ("https://b.edu/", 0),
           ("https://B.edu/people/x", 1)]
    out += [(f"https://c.edu/w{i}", 1) for i in range(250)]
    return out


def test_explore_root_lists_hosts_by_count_with_depths():
    node = ExploreTree(urls()).node([])
    assert node["path"] == [] and node["total"] == 257
    assert [(c["segment"], c["count"]) for c in node["children"]] == [("c.edu", 250), ("a.edu", 5), ("b.edu", 2)]
    assert node["depths"] == [{"label": "0", "value": 2}, {"label": "1", "value": 253}, {"label": "2", "value": 2}]


def test_explore_child_counts_leaves_and_urls_ending_here():
    node = ExploreTree(urls()).node(["a.edu"])
    assert node["total"] == 5
    assert {c["segment"]: (c["count"], c["leaf"]) for c in node["children"]} == {"news": (3, False), "about": (1, True)}
    assert node["urls"] == [{"url": "https://a.edu/", "depth": 0}]
    assert node["depths"] is None
    news = ExploreTree(urls()).node(["a.edu", "news"])
    assert sorted(u["url"] for u in news["urls"]) == ["https://a.edu/news/"]


def test_explore_caps_children_and_reports_the_remainder():
    node = ExploreTree(urls()).node(["c.edu"])
    assert len(node["children"]) == 200
    assert node["other"] == 50


def test_explore_unknown_path_is_none():
    assert ExploreTree(urls()).node(["nope.edu"]) is None


# ---- LiveFeed over a temp lake ----

deltalake = pytest.importorskip("deltalake")
from deltalake import write_deltalake  # noqa: E402

from ccfeed.live import LiveFeed  # noqa: E402


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


def write(base, table, cols: dict, partition=None, mode="append"):
    write_deltalake(str(base / table), pa.table(cols), mode=mode, partition_by=partition)


def make_lake(base, now):
    write(base, "seed_urls", {"url": ["https://a.edu/"], "priority": [0], "_ingestion_time": [iso(now - 600)]})
    write(base, "stage1_discovery", {
        "url": ["https://a.edu/", "https://a.edu/news/one"], "domain": ["a.edu", "a.edu"],
        "depth": [0, 2], "status_code": [200, 200], "_ingestion_time": [iso(now - 500), iso(now - 400)]},
        partition=["domain"])
    write(base, "stage2_page_analysis", {
        "url": ["https://a.edu/news/one"], "word_count": [700], "is_massive_doc": [False],
        "_ingestion_time": [iso(now - 300)]})
    write(base, "stage3_summaries", {"url": ["https://a.edu/news/one"], "model": ["bart"],
                                     "summary": ["Short."], "_ingestion_time": [iso(now - 200)]})
    write(base, "stage4_large_doc_summaries", {"url": ["https://a.edu/big"], "compression_ratio": [0.01],
                                               "_ingestion_time": [iso(now - 100)]})


def test_live_feed_reports_missing_tables_without_counts(tmp_path):
    feed = LiveFeed(tmp_path / "lake")
    res = feed.poll(time.time())
    assert {s.status for s in res.stages.values()} == {"missing"}
    assert all(s.count is None for s in res.stages.values())
    delta = feed.services(time.time())[0]
    assert delta["id"] == "delta" and delta["status"] == "down"


def test_live_feed_polls_counts_then_emits_new_rows_as_events(tmp_path):
    now = time.time()
    make_lake(tmp_path, now)
    feed = LiveFeed(tmp_path)
    first = feed.poll(now)
    assert first.events == []
    assert first.stages["discovery"].count == 2 and first.stages["discovery"].status == "ok"
    assert first.stages["large_docs"].last_write == pytest.approx(now - 100, abs=1e-3)
    write(tmp_path, "stage1_discovery", {"url": ["https://a.edu/new"], "domain": ["a.edu"], "depth": [1],
                                         "status_code": [200], "_ingestion_time": [iso(now + 1)]}, partition=["domain"])
    write(tmp_path, "stage1_errors", {"url": ["https://a.edu/broken"], "error_type": ["timeout"],
                                      "_ingestion_time": [iso(now + 2)]})
    res = feed.poll(now + 2)
    kinds = [(e["kind"], e["url"]) for e in res.events]
    assert kinds == [("discovered", "https://a.edu/new"), ("error", "https://a.edu/broken")]
    assert res.events[0]["fields"] == {"depth": 1, "status_code": 200}
    assert res.events[1]["fields"] == {"error_type": "timeout"}
    assert res.stages["discovery"].count == 3
    assert res.stages["discovery"].errors == 1
    ids = [e["id"] for e in res.events]
    assert len(set(ids)) == 2


def test_live_feed_detail_journey_and_explore(tmp_path):
    now = time.time()
    make_lake(tmp_path, now)
    feed = LiveFeed(tmp_path)
    feed.poll(now)
    detail = feed.stage_detail("discovery")
    assert bars(detail, "depth") == [("0", 1), ("2", 1)]
    assert detail["recent"][0]["url"] == "https://a.edu/news/one"
    assert feed.stage_detail("nope") is None
    j = feed.url_journey("https://a.edu/news/one")
    assert [s["status"] for s in j["steps"]] == ["skipped", "done", "done", "done", "skipped"]
    assert feed.url_journey("https://a.edu/never") is None
    assert feed.explore(["a.edu"])["total"] == 2
    assert feed.explore(["zzz"]) is None


def test_live_feed_backfill_is_cumulative_and_ends_at_the_total(tmp_path):
    now = time.time()
    make_lake(tmp_path, now)
    feed = LiveFeed(tmp_path)
    series = feed.backfill(now, 1800, 10)["discovery"]
    counts = [c for _, c in series]
    assert counts == sorted(counts)
    assert counts[-1] == 2 and counts[0] == 0
    assert len(series) == 180


def test_live_feed_delta_service_row_counts_tables(tmp_path):
    now = time.time()
    make_lake(tmp_path, now)
    feed = LiveFeed(tmp_path)
    feed.poll(now)
    row = feed.services(now)[0]
    assert row["status"] == "ok"
    assert row["data"]["tables_found"] == 5 and row["data"]["tables_total"] == 5
    assert "5/5" in row["detail"]
