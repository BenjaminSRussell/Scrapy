"""#790: AggregationPipeline persists entity summaries and bounds memory."""

import json
from types import SimpleNamespace

from src.pipelines import AggregationPipeline
from src.utils.delta import DeltaHelper


def _items():
    return [
        {"entity_id": "uconn_tuition", "title": "2024", "url": "https://u/2024", "recency_score": 1.0},
        {"entity_id": "uconn_tuition", "title": "2023", "url": "https://u/2023", "recency_score": 0.5},
        {"entity_id": "yale_tuition", "title": "Yale", "url": "https://y/2024", "recency_score": 0.9},
        {"title": "no entity", "url": "https://x"},
    ]


class _RecordingSink:
    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    def write(self, table, rows, mode="append", async_write=True):
        self.calls.append((table, rows, mode, async_write))
        return self.ok


def test_close_writes_one_row_per_entity_synchronously():
    sink = _RecordingSink()
    pipe = AggregationPipeline(enabled=True, delta=sink)
    spider = SimpleNamespace(name="scout")
    for item in _items():
        pipe.process_item(item, spider)
    pipe.close_spider(spider)

    assert len(sink.calls) == 1
    table, rows, mode, async_write = sink.calls[0]
    assert (table, mode, async_write) == ("entity_summaries", "append", False)
    by_id = {r["entity_id"]: r for r in rows}
    assert set(by_id) == {"uconn_tuition", "yale_tuition"}
    assert by_id["uconn_tuition"]["source_count"] == 2
    assert json.loads(by_id["uconn_tuition"]["top_urls"]) == ["https://u/2024", "https://u/2023"]
    assert by_id["uconn_tuition"]["max_recency_score"] == 1.0
    assert by_id["yale_tuition"]["spider"] == "scout"
    assert pipe.summaries_written == 2


def test_memory_is_bounded_per_entity_but_count_is_total():
    sink = _RecordingSink()
    pipe = AggregationPipeline(enabled=True, max_items_per_entity=3, delta=sink)
    spider = SimpleNamespace(name="scout")
    for i in range(50):
        pipe.process_item({"entity_id": "e", "url": f"https://e/{i}", "recency_score": i / 50}, spider)

    assert len(pipe.entity_groups["e"]) <= 3
    pipe.close_spider(spider)
    row = sink.calls[0][1][0]
    assert row["source_count"] == 50
    # The retained items are the most recent ones.
    assert json.loads(row["top_urls"]) == ["https://e/49", "https://e/48", "https://e/47"]


def test_persist_false_and_sink_failure_do_not_raise():
    spider = SimpleNamespace(name="scout")
    off = AggregationPipeline(enabled=True, persist=False, delta=_RecordingSink())
    off.process_item(_items()[0], spider)
    off.close_spider(spider)
    assert off._delta.calls == []

    failing = AggregationPipeline(enabled=True, delta=_RecordingSink(ok=False))
    failing.process_item(_items()[0], spider)
    failing.close_spider(spider)  # logs, does not raise
    assert failing.summaries_written == 0


def test_summaries_are_queryable_from_delta_after_crawl(tmp_path):
    """End-to-end: items in, spider closes, rows readable from the Delta table."""
    delta = DeltaHelper(tmp_path / "lake")
    pipe = AggregationPipeline(enabled=True, delta=delta)
    spider = SimpleNamespace(name="scout")
    for item in _items():
        pipe.process_item(item, spider)
    pipe.close_spider(spider)

    rows = delta.read("entity_summaries")
    try:
        assert {r["entity_id"] for r in rows} == {"uconn_tuition", "yale_tuition"}
        assert all(r["summary"] for r in rows)
    finally:
        delta.manager.shutdown()
