"""#608: Scout's Stage1 -> Stage2 handoff reaches stage2_queue through ITEM_PIPELINES."""

from types import SimpleNamespace

import pytest

from src import pipelines as p
from src.settings import ITEM_PIPELINES
from src.utils.delta import DeltaHelper

STAGE2_DICT = {
    "url": "https://uconn.edu/admissions",
    "parent_url": "https://uconn.edu/",
    "content_hint": "html",
    "priority": 2,
    "status": "pending",
    "queued_at": "2026-10-07T12:00:00",
    "queued_by": "scout",
    "target_stage": "stage2",
}


def test_queue_pipeline_registered_before_schema_validation_and_kafka():
    queue = ITEM_PIPELINES["src.pipelines.QueueItemPipeline"]
    assert queue < ITEM_PIPELINES["src.pipelines.SchemaValidationPipeline"]
    assert queue < ITEM_PIPELINES["src.pipelines.KafkaPipeline"]
    assert queue > ITEM_PIPELINES["src.pipelines.DataValidationPipeline"]


def test_schema_validation_and_kafka_pass_routing_items_through():
    spider = SimpleNamespace(name="scout")
    schema = p.SchemaValidationPipeline(enabled=True)
    item = dict(STAGE2_DICT)
    assert schema.process_item(item, spider) is item  # no DropItem

    kafka = p.KafkaPipeline.__new__(p.KafkaPipeline)
    kafka.producer = None  # would raise DropItem for a content record
    assert kafka.process_item(item, spider) is item


def test_non_routing_dicts_pass_queue_pipeline_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))
    queue = p.QueueItemPipeline()
    content = {"url": "https://uconn.edu/x", "title": "X"}
    assert queue.process_item(content, SimpleNamespace(name="scout")) is content
    assert queue.items_processed == 0 and queue.stage2_queue_batch == []


@pytest.mark.parametrize("target", ["stage2", "javascript"])
def test_scout_dict_lands_in_queue_table(tmp_path, monkeypatch, target):
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))
    spider = SimpleNamespace(name="scout")
    item = dict(STAGE2_DICT)
    table = "stage2_queue"
    if target == "javascript":
        item.pop("target_stage")
        item.pop("content_hint")
        item["target_spider"] = "javascript"
        table = "js_spider_queue"

    queue = p.QueueItemPipeline()
    queue.delta = DeltaHelper(tmp_path / "lake")
    # Registered order up to and past the queue pipeline.
    chain = [
        p.DataValidationPipeline(),
        p.DataCleansingPipeline(),
        queue,
        p.SchemaValidationPipeline(enabled=True),
        p.MetadataPipeline(),
    ]
    for pipe in chain:
        item = pipe.process_item(item, spider)

    # Flush synchronously so the assertion does not race the writer thread.
    rows = list(queue.js_queue_batch if target == "javascript" else queue.stage2_queue_batch)
    queue.delta.write(table, rows, mode="append", async_write=False)
    queue.js_queue_batch.clear()
    queue.stage2_queue_batch.clear()

    try:
        stored = queue.delta.read(table)
        assert [r["url"] for r in stored] == ["https://uconn.edu/admissions"]
        assert stored[0]["status"] == "pending"
        # Metadata ran after the queue pipeline but did not leak into the row.
        assert "pipeline_version" not in stored[0]
    finally:
        queue.delta.manager.shutdown()
