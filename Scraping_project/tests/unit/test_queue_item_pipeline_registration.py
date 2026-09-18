"""Tests for QueueItemPipeline registration and Stage1→Stage2 handoff (#608)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.pipelines import QueueItemPipeline, SchemaValidationPipeline
from src.queue_routing import is_queue_routing_item


@pytest.mark.unit
def test_queue_item_pipeline_registered_at_350():
    """QueueItemPipeline must be in default ITEM_PIPELINES before Kafka (400)."""
    from src import settings

    pipelines = settings.ITEM_PIPELINES
    assert "src.pipelines.QueueItemPipeline" in pipelines
    assert pipelines["src.pipelines.QueueItemPipeline"] == 350

    schema_pri = pipelines["src.pipelines.SchemaValidationPipeline"]
    kafka_pri = pipelines["src.pipelines.KafkaPipeline"]
    queue_pri = pipelines["src.pipelines.QueueItemPipeline"]
    assert schema_pri < queue_pri < kafka_pri


@pytest.mark.unit
def test_is_queue_routing_item_detects_scout_dicts():
    assert is_queue_routing_item({"url": "https://example.com", "target_stage": "stage2"})
    assert is_queue_routing_item({"url": "https://example.com", "target_spider": "javascript"})
    assert not is_queue_routing_item({"url": "https://example.com", "title": "page"})
    assert not is_queue_routing_item(MagicMock())


@pytest.mark.unit
def test_schema_validation_does_not_drop_stage2_queue_dict():
    """Dict queue items must survive SchemaValidation (no DropItem)."""
    # Import settings to apply #608 SchemaValidation skip patch.
    import src.settings  # noqa: F401

    pipeline = SchemaValidationPipeline(enabled=True)
    spider = MagicMock()
    spider.name = "scout"

    item = {
        "url": "https://example.com/page",
        "parent_url": "https://example.com",
        "content_hint": "html",
        "priority": 2,
        "status": "pending",
        "queued_at": "2026-09-18T12:00:00",
        "queued_by": "scout",
        "target_stage": "stage2",
    }

    out = pipeline.process_item(item, spider)
    assert out is item
    assert out["target_stage"] == "stage2"


@pytest.mark.unit
def test_scout_stage2_dict_writes_stage2_queue_on_close():
    """Smoke: ≥1 stage2 dict → stage2_queue write via QueueItemPipeline."""
    mock_delta = MagicMock()
    spider = MagicMock()
    spider.name = "scout"

    with patch("src.utils.delta.get_delta", return_value=mock_delta):
        pipeline = QueueItemPipeline()

    stage2_item = {
        "url": "https://example.com/research",
        "parent_url": "https://example.com",
        "content_hint": "html",
        "priority": 2,
        "status": "pending",
        "queued_at": "2026-09-18T12:00:00",
        "queued_by": "scout",
        "target_stage": "stage2",
    }

    out = pipeline.process_item(stage2_item, spider)
    assert out is stage2_item
    assert pipeline.items_processed == 1
    assert len(pipeline.stage2_queue_batch) == 1

    pipeline.spider_closed(spider)

    mock_delta.write.assert_called()
    table_names = [c.args[0] for c in mock_delta.write.call_args_list]
    assert "stage2_queue" in table_names

    for call in mock_delta.write.call_args_list:
        if call.args[0] == "stage2_queue":
            rows = call.args[1]
            assert len(rows) >= 1
            assert rows[0]["url"] == "https://example.com/research"
            assert rows[0]["target_stage"] == "stage2"
            assert call.kwargs.get("mode") == "append"
            break
    else:
        pytest.fail("stage2_queue write not found")


@pytest.mark.unit
def test_javascript_queue_dict_writes_js_spider_queue_on_close():
    mock_delta = MagicMock()
    spider = MagicMock()
    spider.name = "scout"

    with patch("src.utils.delta.get_delta", return_value=mock_delta):
        pipeline = QueueItemPipeline()

    js_item = {
        "url": "https://example.com/spa",
        "parent_url": "https://example.com",
        "priority": 1,
        "status": "pending",
        "queued_at": "2026-09-18T12:00:00",
        "queued_by": "scout",
        "target_spider": "javascript",
    }

    pipeline.process_item(js_item, spider)
    pipeline.spider_closed(spider)

    table_names = [c.args[0] for c in mock_delta.write.call_args_list]
    assert "js_spider_queue" in table_names
