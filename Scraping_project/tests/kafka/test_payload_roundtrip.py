"""Kafka produce -> consume contract through the real producers (#281).

Each Stage 1 item runs SchemaValidationPipeline (200), MetadataPipeline (250),
RecencyScoringPipeline (300) and KafkaPipeline (400), the order ITEM_PIPELINES
uses. The producer is an in-memory fake and the consumer decodes UTF-8 JSON, the
serde kafka-delta-ingest reads, so no broker is needed. The module carries the
``kafka`` marker: it runs with ``pytest -m kafka``, not in the default selection.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scrapy.exceptions import DropItem

from src import pipelines
from src.core.ingest_contract import missing_ingest_fields
from src.lakehouse.seed_manager import default_url_hasher
from src.schemas import ValidationFailureRecord
from src.utils import kafka_topics

pytestmark = pytest.mark.kafka

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "kafka"
SPIDER = SimpleNamespace(name="scout")
# Stamped per run (clock, failure time, pydantic wording): the golden fixes only their type.
VOLATILE = {"scraped_at_utc", "failed_at_utc", "error_message"}


def _golden(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeProducer:
    """Stands in for confluent_kafka.Producer and records what it was asked to send."""

    def __init__(self):
        self.sent = []

    def produce(self, topic, value, key=None, callback=None):
        self.sent.append({"topic": topic, "key": key, "value": value})

    def poll(self, timeout=0):
        return 0

    def flush(self, timeout=None):
        return 0


def _consume(message: dict) -> dict:
    return json.loads(message["value"].decode("utf-8"))


def _assert_matches_golden(wire: dict, golden: dict) -> None:
    lost = sorted(set(golden) - set(wire))
    assert not lost, f"payload dropped fields the golden contract names: {lost}"
    for name, expected in golden.items():
        if name in VOLATILE:
            assert type(wire[name]) is type(expected), name
        elif expected is not None:  # nullable on the wire: presence is enough
            assert wire[name] == expected, name


def test_stage1_payload_keeps_the_golden_wire_shape(tmp_path):
    golden = _golden("scraped_item.json")
    producer = FakeProducer()
    kafka = pipelines.KafkaPipeline(
        "broker:9092", kafka_topics.DEFAULT_TOPIC_NAMES["scraped_items"], spill_dir=tmp_path
    )
    kafka.producer = producer
    item = {"url": golden["url"], "title": golden["title"], "content": golden["content"]}
    for stage in (
        pipelines.SchemaValidationPipeline(enabled=True),
        pipelines.MetadataPipeline(),
        pipelines.RecencyScoringPipeline(),
    ):
        item = stage.process_item(item, SPIDER)
    kafka.process_item(item, SPIDER)

    (message,) = producer.sent
    assert message["topic"] == kafka_topics.DEFAULT_TOPIC_NAMES["scraped_items"]
    assert message["key"] == default_url_hasher(golden["url"]).encode()  # #285: one URL, one partition
    wire = _consume(message)
    assert missing_ingest_fields(wire) == []
    _assert_matches_golden(wire, golden)


def test_validation_failure_keeps_the_golden_wire_shape():
    golden = _golden("validation_failure.json")
    producer = FakeProducer()
    schema = pipelines.SchemaValidationPipeline(enabled=True)
    schema.kafka_producer = producer
    with pytest.raises(DropItem):
        schema.process_item({"url": golden["url"], "tuition_cost": -5}, SPIDER)

    (message,) = producer.sent
    assert message["topic"] == kafka_topics.DEFAULT_TOPIC_NAMES["validation_failures"]
    wire = _consume(message)
    ValidationFailureRecord.model_validate(wire)
    _assert_matches_golden(wire, golden)
