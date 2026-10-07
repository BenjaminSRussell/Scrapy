"""#201: AggregationPipeline must not hold every entity until spider close."""

from types import SimpleNamespace

from src.pipelines import AggregationPipeline

SPIDER = SimpleNamespace(name="scout")


class Sink:
    def __init__(self):
        self.rows = []

    def write(self, table, rows, mode="append", async_write=True):
        assert async_write is False
        self.rows.extend(rows)
        return True


def _item(eid, n=0):
    return {"entity_id": eid, "title": f"t{n}", "content": "c", "recency_score": n, "url": f"https://u.edu/{eid}/{n}"}


def test_max_entities_caps_memory_and_spills_lru():
    sink = Sink()
    pipe = AggregationPipeline(delta=sink, max_entities=50, flush_every_items=0)
    for i in range(1000):
        pipe.process_item(_item(f"e{i}"), SPIDER)
        assert len(pipe.entity_groups) <= 50
    assert pipe.flushes > 0 and len(sink.rows) >= 1000 - 50
    pipe.close_spider(SPIDER)
    assert sorted(r["entity_id"] for r in sink.rows) == sorted(f"e{i}" for i in range(1000))
    assert all(r["spider"] == "scout" for r in sink.rows)


def test_recently_touched_entity_survives_spill():
    sink = Sink()
    pipe = AggregationPipeline(delta=sink, max_entities=10, flush_every_items=0)
    pipe.process_item(_item("hot", 0), SPIDER)
    for i in range(30):
        pipe.process_item(_item(f"cold{i}"), SPIDER)
        pipe.process_item(_item("hot", i + 1), SPIDER)  # keep "hot" most recent
    assert "hot" in pipe.entity_groups
    assert not any(r["entity_id"] == "hot" for r in sink.rows)
    assert pipe.entity_counts["hot"] == 31


def test_periodic_flush_before_close():
    sink = Sink()
    pipe = AggregationPipeline(delta=sink, max_entities=10_000, flush_every_items=100)
    for i in range(250):
        pipe.process_item(_item(f"e{i % 7}", i), SPIDER)
    assert pipe.flushes == 2 and pipe.items_since_flush == 50
    assert sum(r["source_count"] for r in sink.rows) == 200
    pipe.close_spider(SPIDER)
    assert sum(r["source_count"] for r in sink.rows) == 250


def test_flush_without_persist_still_frees_memory():
    pipe = AggregationPipeline(persist=False, max_entities=5, flush_every_items=0)
    for i in range(100):
        pipe.process_item(_item(f"e{i}"), SPIDER)
    assert len(pipe.entity_groups) <= 5 and pipe.summaries_written == 0


def test_from_crawler_reads_settings():
    from scrapy.settings import Settings

    crawler = SimpleNamespace(
        settings=Settings({"AGGREGATION_MAX_ENTITIES": 7, "AGGREGATION_FLUSH_EVERY_ITEMS": 0}),
        signals=SimpleNamespace(connect=lambda *a, **k: None),
    )
    pipe = AggregationPipeline.from_crawler(crawler)
    assert pipe.max_entities == 7 and pipe.flush_every_items == 0
