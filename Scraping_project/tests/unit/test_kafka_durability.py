"""#175 produce failures retry then spill; #249 undelivered-on-close spills."""

import json
from types import SimpleNamespace

import pytest
from scrapy.exceptions import DropItem

from src import pipelines as p

SPIDER = SimpleNamespace(name="scout")


class FakeMsg:
    def __init__(self, value):
        self._v = value

    def value(self):
        return self._v

    def topic(self):
        return "t"

    def partition(self):
        return 0

    def offset(self):
        return 1


class FakeProducer:
    """Queues messages; poll/flush deliver them (or fail) via callbacks."""

    def __init__(self, produce_failures=0, deliver_error=None, flush_leaves=0):
        self.produce_failures = produce_failures
        self.deliver_error = deliver_error
        self.flush_leaves = flush_leaves
        self.queue = []
        self.produce_calls = 0

    def produce(self, topic, value, callback):
        self.produce_calls += 1
        if self.produce_failures > 0:
            self.produce_failures -= 1
            raise BufferError("Local: Queue full")
        self.queue.append((value, callback))

    def _deliver(self, keep=0):
        while len(self.queue) > keep:
            value, cb = self.queue.pop(0)
            cb(self.deliver_error, FakeMsg(value))

    def poll(self, timeout=0):
        if self.flush_leaves == 0:
            self._deliver()
        return 0

    def flush(self, timeout=None):
        self._deliver(keep=self.flush_leaves)
        return len(self.queue)


def _pipe(tmp_path, producer, retries=3):
    k = p.KafkaPipeline("broker:9092", "items", spill_dir=tmp_path / "spill",
                        produce_retries=retries, retry_backoff=0.0)
    k.producer = producer
    return k


def _spilled(tmp_path):
    path = tmp_path / "spill" / "items.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _metric(m, reason):
    return m.labels(reason=reason)._value.get() if m is not None else None


def test_transient_produce_error_is_retried(tmp_path):
    k = _pipe(tmp_path, FakeProducer(produce_failures=2))
    item = {"url": "https://uconn.edu/a", "title": "A"}
    assert k.process_item(item, SPIDER) is item
    assert k.producer.produce_calls == 3 and k.messages_sent == 1
    assert _spilled(tmp_path) == []


def test_exhausted_produce_spills_instead_of_dropping(tmp_path):
    before = _metric(p.KAFKA_PRODUCE_FAILURES, "produce_error")
    k = _pipe(tmp_path, FakeProducer(produce_failures=99))
    item = {"url": "https://uconn.edu/b", "title": "B"}
    assert k.process_item(item, SPIDER) is item  # not DropItem: durably captured
    rows = _spilled(tmp_path)
    assert len(rows) == 1 and rows[0]["reason"] == "produce_error"
    assert json.loads(rows[0]["value"])["url"] == "https://uconn.edu/b"
    if before is not None:
        assert _metric(p.KAFKA_PRODUCE_FAILURES, "produce_error") == before + 1


def test_async_delivery_failure_is_spilled(tmp_path):
    k = _pipe(tmp_path, FakeProducer(deliver_error="Broker: Not enough in-sync replicas"))
    k.process_item({"url": "https://uconn.edu/c"}, SPIDER)
    rows = _spilled(tmp_path)
    assert [r["reason"] for r in rows] == ["delivery_failed"]
    assert "in-sync" in rows[0]["error"] and k.messages_failed == 1 and not k._inflight


def test_undelivered_after_close_flush_is_spilled(tmp_path):
    before = _metric(p.KAFKA_PRODUCE_FAILURES, "undelivered_on_close")
    k = _pipe(tmp_path, FakeProducer(flush_leaves=2))
    for i in range(5):
        k.process_item({"url": f"https://uconn.edu/{i}"}, SPIDER)
    k.close_spider(SPIDER)
    rows = _spilled(tmp_path)
    assert k.messages_sent == 3
    assert [json.loads(r["value"])["url"] for r in rows] == ["https://uconn.edu/3", "https://uconn.edu/4"]
    assert all(r["reason"] == "undelivered_on_close" for r in rows)
    if before is not None:
        assert _metric(p.KAFKA_PRODUCE_FAILURES, "undelivered_on_close") == before + 2


def test_clean_close_spills_nothing(tmp_path):
    k = _pipe(tmp_path, FakeProducer())
    k.process_item({"url": "https://uconn.edu/ok"}, SPIDER)
    k.close_spider(SPIDER)
    assert _spilled(tmp_path) == [] and not k._inflight and k.messages_sent == 1


def test_drop_only_when_spill_also_fails(tmp_path, monkeypatch):
    k = _pipe(tmp_path, FakeProducer(produce_failures=99))
    monkeypatch.setattr(k, "_spill", lambda *a, **kw: False)
    with pytest.raises(DropItem):
        k.process_item({"url": "https://uconn.edu/x"}, SPIDER)


def test_settings_wired_through_from_crawler(monkeypatch):
    from scrapy.settings import Settings

    monkeypatch.setattr(p, "KAFKA_AVAILABLE", True)
    crawler = SimpleNamespace(
        settings=Settings({"KAFKA_BOOTSTRAP_SERVERS": "b:9092", "KAFKA_TOPIC": "items",
                           "KAFKA_SPILL_DIR": "/tmp/s", "KAFKA_PRODUCE_RETRIES": 5,
                           "KAFKA_CLOSE_FLUSH_TIMEOUT": 12}),
        signals=SimpleNamespace(connect=lambda *a, **k: None),
    )
    k = p.KafkaPipeline.from_crawler(crawler)
    assert (str(k.spill_dir), k.produce_retries, k.close_flush_timeout) == ("/tmp/s", 5, 12.0)
