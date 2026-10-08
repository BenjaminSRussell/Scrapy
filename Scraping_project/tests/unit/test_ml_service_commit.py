"""#252: ZSC microservice commits offsets manually, only after the sink acknowledged."""

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("confluent_kafka")

from src import ml_service as ms  # noqa: E402
from src.schemas import CategoryType  # noqa: E402


class Msg:
    def __init__(self, value, offset=0, topic="validated_items", partition=0):
        self._v = value if isinstance(value, bytes) else json.dumps(value).encode()
        self._o, self._t, self._p = offset, topic, partition

    def value(self):
        return self._v

    def offset(self):
        return self._o

    def topic(self):
        return self._t

    def partition(self):
        return self._p

    def error(self):
        return None


class FakeConsumer:
    def __init__(self, messages=()):
        self.config = None
        self.messages = list(messages)
        self.commits: list[int] = []
        self.seeks: list[tuple[str, int, int]] = []
        self.closed = False

    def subscribe(self, topics):
        self.topics = topics

    def poll(self, timeout=None):
        if not self.messages:
            raise KeyboardInterrupt  # end the start() loop
        return self.messages.pop(0)

    def commit(self, message=None, asynchronous=True):
        assert asynchronous is False, "commit must be synchronous"
        self.commits.append(message.offset())

    def seek(self, tp):
        self.seeks.append((tp.topic, tp.partition, tp.offset))

    def close(self):
        self.closed = True


class FakeProducer:
    """Delivers on flush; `fail` = delivery error, `drop` = never acknowledged, `raise_` = produce raises."""

    def __init__(self, fail=0, drop=0, raise_=0):
        self.fail, self.drop, self.raise_ = fail, drop, raise_
        self.pending = []
        self.delivered: list[tuple[str, dict]] = []

    def produce(self, topic, value, callback=None, key=None):
        if self.raise_:
            self.raise_ -= 1
            raise BufferError("Local: Queue full")
        self.pending.append((topic, value, callback))

    def poll(self, timeout=0):
        return 0

    def flush(self, timeout=None):
        while self.pending:
            topic, value, cb = self.pending.pop(0)
            if self.drop:
                self.drop -= 1
                continue
            if self.fail:
                self.fail -= 1
                cb("BROKER_DOWN", None)
                continue
            self.delivered.append((topic, json.loads(value)))
            cb(None, SimpleNamespace(topic=lambda: topic))
        return 0


class FakeClassifier:
    confidence_threshold = 0.85

    def __init__(self, confidence=0.95, fail=0):
        self.confidence, self.fail = confidence, fail

    def classify(self, text):
        if self.fail:
            self.fail -= 1
            raise RuntimeError("CUDA OOM")
        return {"category": list(CategoryType)[0], "confidence": self.confidence,
                "meets_threshold": self.confidence >= self.confidence_threshold}


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(ms, "ZeroShotClassifier", lambda **kw: FakeClassifier())
    monkeypatch.setenv("ZSC_RETRY_BACKOFF", "0")
    svc = ms.ZSCMicroservice()
    svc.consumer = FakeConsumer()
    svc.producer = FakeProducer()
    return svc


ITEM = {"url": "https://uconn.edu/a", "title": "Admissions", "content": "apply now"}


def test_consumer_config_disables_auto_commit(monkeypatch, service):
    captured = {}

    def make_consumer(config):
        captured.update(config)
        return FakeConsumer()

    monkeypatch.setattr(ms, "Consumer", make_consumer)
    monkeypatch.setattr(ms, "Producer", lambda config: FakeProducer())
    service.start()
    assert captured["enable.auto.commit"] is False
    assert captured["enable.auto.offset.store"] is False


def test_commit_only_after_delivery(service):
    service._handle(Msg(ITEM, offset=7))
    assert service.producer.delivered[0][0] == "final_categorized"
    assert service.consumer.commits == [7] and service.consumer.seeks == []


def test_low_confidence_goes_to_review_topic_then_commits(service):
    service.classifier = FakeClassifier(confidence=0.2)
    service._handle(Msg(ITEM, offset=3))
    assert service.producer.delivered[0][0] == "low_confidence_review"
    assert service.consumer.commits == [3]


@pytest.mark.parametrize("producer", [FakeProducer(fail=1), FakeProducer(drop=1), FakeProducer(raise_=1)],
                         ids=["delivery-error", "no-ack", "produce-raises"])
def test_sink_failure_does_not_commit_and_replays(service, producer):
    service.producer = producer
    out = service._handle(Msg(ITEM, offset=11))
    assert out == ms.MessageOutcome.RETRY
    assert service.consumer.commits == []
    assert service.consumer.seeks == [("validated_items", 0, 11)]
    # the replay succeeds -> committed exactly once
    service._handle(Msg(ITEM, offset=11))
    assert service.consumer.commits == [11]
    assert len(service.producer.delivered) == 1


def test_classifier_error_is_retried_not_committed(service):
    service.classifier = FakeClassifier(fail=1)
    assert service._handle(Msg(ITEM, offset=4)) == ms.MessageOutcome.RETRY
    assert service.consumer.commits == []
    assert service._handle(Msg(ITEM, offset=4)) == ms.MessageOutcome.DONE
    assert service.consumer.commits == [4]


def test_poison_message_is_committed_after_max_attempts(service, caplog):
    service.max_message_attempts = 3
    service.classifier = FakeClassifier(fail=99)
    for _ in range(3):
        service._handle(Msg(ITEM, offset=9))
    assert service.consumer.seeks == [("validated_items", 0, 9)] * 2
    assert service.consumer.commits == [9]
    assert service.items_given_up == 1
    assert any("Giving up" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("raw", [b"{not json", b"\xff\xfe", b"[1, 2]"])
def test_unparseable_input_is_skipped_and_committed(service, raw):
    assert service._handle(Msg(raw, offset=5)) == ms.MessageOutcome.SKIP
    assert service.consumer.commits == [5] and service.producer.delivered == []


def test_item_without_text_is_skipped_and_committed(service):
    assert service._handle(Msg({"url": "https://uconn.edu/empty"}, offset=6)) == ms.MessageOutcome.SKIP
    assert service.consumer.commits == [6]


def test_start_loop_processes_and_commits_each_message(monkeypatch, service):
    consumer = FakeConsumer([Msg(ITEM, offset=0), Msg(ITEM, offset=1)])
    monkeypatch.setattr(ms, "Consumer", lambda config: consumer)
    monkeypatch.setattr(ms, "Producer", lambda config: FakeProducer())
    service.start()
    assert consumer.commits == [0, 1] and consumer.closed
