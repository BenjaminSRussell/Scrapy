"""#660: Kafka unavailable while the crawler publishes (KafkaPipeline).

A scripted broker (no network, no confluent_kafka broker) goes down for a window
and comes back. Every item must end up delivered or spilled+dead-lettered,
exactly once: no silent drop, no duplicate produce on recovery.
Complements tests/unit/test_kafka_durability.py (single-message retry/spill/close).
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from src import pipelines as p

SPIDER = SimpleNamespace(name="scout")


class KafkaDown(Exception):
    """Stands in for confluent_kafka.KafkaException(_TRANSPORT / _ALL_BROKERS_DOWN)."""


class Msg:
    def __init__(self, value):
        self._v = value

    def value(self):
        return self._v

    def topic(self):
        return "items"

    def partition(self):
        return 0

    def offset(self):
        return 0


class ScriptedBroker:
    """Producer whose broker is up/down on command.

    ``down``: produce() raises (local queue full / all brokers down).
    ``fail_delivery``: produce() is accepted, but delivery later fails
    (broker died after enqueue). ``poll_raises``: the next poll() raises once
    after produce() already enqueued the message.
    """

    def __init__(self):
        self.down = False
        self.fail_delivery = False
        self.poll_raises = 0
        self.queue: list[tuple[bytes, object]] = []
        self.delivered: list[bytes] = []
        self.produce_calls = 0
        self.poll_timeouts: list[float] = []

    def produce(self, topic, value, callback, key=None):
        self.produce_calls += 1
        if self.down:
            raise KafkaDown("Local: All broker connections are down")
        self.queue.append((value, callback))

    def _serve(self):
        while self.queue:
            value, cb = self.queue.pop(0)
            if self.fail_delivery:
                cb(KafkaDown("Broker: Not enough in-sync replicas"), Msg(value))
            else:
                self.delivered.append(value)
                cb(None, Msg(value))

    def poll(self, timeout=0):
        self.poll_timeouts.append(timeout)
        if self.poll_raises:
            self.poll_raises -= 1
            raise KafkaDown("Fatal: callback raised / transport error during poll")
        if not self.down:
            self._serve()
        return 0

    def flush(self, timeout=None):
        if not self.down:
            self._serve()
        return len(self.queue)


def _pipe(tmp_path, broker, retries=3, backoff=0.0):
    k = p.KafkaPipeline("broker:9092", "items", spill_dir=tmp_path / "spill",
                        produce_retries=retries, retry_backoff=backoff, close_flush_timeout=0.01)
    k._dlq = _ListDLQ()
    k.producer = broker
    return k


class _ListDLQ:
    def __init__(self):
        self.entries = []

    def add(self, item, error, stage, context):
        self.entries.append((item, str(error), stage, context))


def _spilled(tmp_path):
    path = tmp_path / "spill" / "items.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _item(i):
    return {"url": f"https://www.uconn.edu/{i}", "url_hash": f"h{i}", "title": f"t{i}"}


def _urls_delivered(broker):
    return [json.loads(v)["url"] for v in broker.delivered]


def _urls_spilled(tmp_path):
    return [json.loads(r["value"])["url"] for r in _spilled(tmp_path)]


def test_outage_window_then_recovery_accounts_for_every_item_once(tmp_path, caplog):
    broker = ScriptedBroker()
    k = _pipe(tmp_path, broker, retries=3)
    with caplog.at_level(logging.WARNING):
        for i in range(0, 3):
            k.process_item(_item(i), SPIDER)          # broker up
        broker.down = True
        for i in range(3, 6):
            k.process_item(_item(i), SPIDER)          # outage: retries then spill
        broker.down = False
        for i in range(6, 9):
            k.process_item(_item(i), SPIDER)          # recovered
        k.close_spider(SPIDER)

    delivered, spilled = _urls_delivered(broker), _urls_spilled(tmp_path)
    everything = [_item(i)["url"] for i in range(9)]
    assert sorted(delivered + spilled) == sorted(everything), "an item was dropped or duplicated"
    assert set(delivered).isdisjoint(spilled)
    assert spilled == [_item(i)["url"] for i in (3, 4, 5)]
    assert {r["reason"] for r in _spilled(tmp_path)} == {"produce_error"}
    assert all("All broker connections are down" in r["error"] for r in _spilled(tmp_path))
    # Spilled items are also dead-lettered for the ops CLI (#162).
    assert [e[0]["url"] for e in k._dlq.entries] == spilled and {e[2] for e in k._dlq.entries} == {"kafka"}
    # Retry and terminal failure are logged.
    assert caplog.text.count("Kafka produce failed (attempt") == 3 * 2
    assert caplog.text.count("Kafka produce failed after 3 attempts") == 3
    assert k.messages_sent == 6 and k.messages_spilled == 3 and not k._inflight


def test_retry_backoff_grows_linearly_and_serves_callbacks(tmp_path):
    broker = ScriptedBroker()
    broker.down = True
    k = _pipe(tmp_path, broker, retries=4, backoff=0.5)
    k.process_item(_item(0), SPIDER)
    assert broker.produce_calls == 4
    # Between attempts the pipeline polls (serving callbacks frees queue space)
    # with backoff * attempt; no poll after the final attempt.
    assert broker.poll_timeouts == [0.5, 1.0, 1.5]


def test_broker_recovers_mid_retry_produces_exactly_once(tmp_path):
    broker = ScriptedBroker()
    k = _pipe(tmp_path, broker, retries=3)
    real_produce = broker.produce

    def flaky(topic, value, callback, key=None):
        broker.down = broker.produce_calls < 1  # first attempt fails, second succeeds
        return real_produce(topic, value, callback, key=key)

    broker.produce = flaky
    k.process_item(_item(0), SPIDER)
    k.close_spider(SPIDER)
    assert _urls_delivered(broker) == [_item(0)["url"]]
    assert _spilled(tmp_path) == []


def test_broker_dies_after_enqueue_delivery_failure_spilled_once(tmp_path):
    broker = ScriptedBroker()
    broker.fail_delivery = True
    k = _pipe(tmp_path, broker)
    for i in range(3):
        k.process_item(_item(i), SPIDER)
    k.close_spider(SPIDER)
    assert broker.delivered == []
    assert _urls_spilled(tmp_path) == [_item(i)["url"] for i in range(3)]
    assert {r["reason"] for r in _spilled(tmp_path)} == {"delivery_failed"}
    assert k.messages_failed == 3 and k.messages_sent == 0


def test_outage_at_shutdown_spills_inflight_once(tmp_path):
    broker = ScriptedBroker()
    k = _pipe(tmp_path, broker)
    broker.down = False
    real_poll = broker.poll
    broker.poll = lambda timeout=0: 0          # nothing gets served before close
    for i in range(2):
        k.process_item(_item(i), SPIDER)
    broker.poll = real_poll
    broker.down = True                         # broker gone: close-time flush can't deliver
    k.close_spider(SPIDER)
    assert _urls_spilled(tmp_path) == [_item(0)["url"], _item(1)["url"]]
    assert {r["reason"] for r in _spilled(tmp_path)} == {"undelivered_on_close"}
    assert not k._inflight


def test_poll_failure_after_successful_produce_does_not_duplicate(tmp_path):
    """produce() already enqueued the message; a raising poll(0) must not re-produce it."""
    broker = ScriptedBroker()
    broker.poll_raises = 1
    k = _pipe(tmp_path, broker)
    k.process_item(_item(0), SPIDER)
    k.close_spider(SPIDER)
    assert broker.produce_calls == 1, "message was produced again after it was already enqueued"
    assert _urls_delivered(broker) == [_item(0)["url"]]
    assert _spilled(tmp_path) == []
