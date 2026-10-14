"""#701: a poison payload is isolated onto the DLQ instead of retrying forever.

No broker process. ``consume_poison_path`` polls a mocked broker, classifies
malformed bytes, invalid JSON, and schema-invalid objects, and commits only
after the bounded retry policy publishes the original bytes.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_policy():
    path = ROOT / "src" / "utils" / "kafka_poison_policy.py"
    spec = importlib.util.spec_from_file_location("kafka_poison_policy_701", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


policy = _load_policy()
Delivery = policy.Delivery

MAIN_RS = ROOT / "kafka-delta-ingest" / "src" / "main.rs"
CONFIG = ROOT / "config.yml"
CONTRACT = ROOT / "src" / "core" / "ingest_contract.py"

GOOD = (
    b'{"url":"https://example.edu/a","scraped_at_utc":"2026-10-07T22:30:00Z",'
    b'"spider_name":"discovery"}'
)
MALFORMED = b"\xff\xfe"
INVALID_JSON = b"{not json"
SCHEMA_INVALID = (
    b'{"url":12,"scraped_at_utc":"2026-10-07T22:30:00Z","spider_name":"discovery"}'
)
TOPIC = "scraped-items"
WHEN_MS = 1_700_000_000_000


def _configured_dlq_topic() -> str:
    match = re.search(r"^\s*dead_letter:\s*(\S+)", CONFIG.read_text(), re.M)
    assert match, "kafka.topics.dead_letter missing from config.yml"
    return match.group(1)


class MockBroker:
    """Holds the uncommitted head until ``commit`` advances it."""

    def __init__(self, deliveries: list) -> None:
        self._log = list(deliveries)
        self._pos = 0
        self.polls = 0
        self.published: list[tuple] = []
        self.commits: list[tuple[str, int, int]] = []
        self.fail_publish = False

    def poll(self):
        self.polls += 1
        if self._pos >= len(self._log):
            return None
        return self._log[self._pos]

    def publish(self, topic, payload, key, headers) -> None:
        if self.fail_publish:
            raise RuntimeError("DLQ produce failed")
        self.published.append((topic, payload, key, dict(headers)))

    def commit(self, topic, partition, next_offset) -> None:
        head = self._log[self._pos]
        if topic != head.topic or partition != head.partition or next_offset != head.offset + 1:
            raise AssertionError(f"commit {topic}[{partition}]@{next_offset} is not the head")
        self.commits.append((topic, partition, next_offset))
        self._pos += 1


def _feed() -> list:
    return [
        Delivery(TOPIC, 0, 0, MALFORMED, key=b"bad-bytes"),
        Delivery(TOPIC, 0, 1, INVALID_JSON, key=b"bad-json"),
        Delivery(TOPIC, 0, 2, SCHEMA_INVALID, key=b"bad-schema"),
        Delivery(TOPIC, 0, 3, GOOD, key=b"ok"),
    ]


def test_classify_covers_malformed_json_and_schema():
    assert policy.classify(None).reason == "empty_payload"
    assert policy.classify(b"").reason == "empty_payload"
    malformed = policy.classify(MALFORMED)
    invalid = policy.classify(INVALID_JSON)
    schema = policy.classify(SCHEMA_INVALID)
    assert malformed.reason == "parse_failed" and "utf-8" in malformed.detail
    assert invalid.reason == "parse_failed" and invalid.detail != malformed.detail
    assert schema.reason == "schema_validation_failed" and "url" in schema.detail
    blank = (
        b'{"url":" ","scraped_at_utc":"2026-10-07T22:30:00Z","spider_name":"discovery"}'
    )
    assert policy.classify(blank).reason == "missing_required_field"
    assert policy.classify(GOOD) is None
    contract = CONTRACT.read_text()
    for field in policy.REQUIRED_INGEST_FIELDS:
        assert f'"{field}"' in contract


def test_poison_pills_go_to_the_configured_dlq_then_the_next_message_is_processed():
    dlq_topic = _configured_dlq_topic()
    assert dlq_topic == policy.DEFAULT_DLQ_TOPIC
    broker = MockBroker(_feed())
    report = policy.consume_poison_path(
        broker,
        dlq_topic=dlq_topic,
        max_failures=3,
        now_ms=lambda: WHEN_MS,
    )

    assert report.attempts == [3, 3, 3, 1]
    # One poll per delivery, then one more that finds the partition caught up.
    assert broker.polls == 5
    assert len(broker.published) == 3
    assert [item.payload for item in report.dead_lettered] == [
        MALFORMED,
        INVALID_JSON,
        SCHEMA_INVALID,
    ]
    reasons = [item.headers["dlq.reason"] for item in report.dead_lettered]
    assert reasons == ["parse_failed", "parse_failed", "schema_validation_failed"]
    assert "utf-8" in report.dead_lettered[0].headers["dlq.error"]
    assert report.dead_lettered[1].headers["dlq.error"] != report.dead_lettered[0].headers["dlq.error"]
    assert "url" in report.dead_lettered[2].headers["dlq.error"]
    for item, offset, key in zip(
        report.dead_lettered, ("0", "1", "2"), (b"bad-bytes", b"bad-json", b"bad-schema"), strict=True
    ):
        assert item.topic == dlq_topic
        assert item.key == key
        assert item.headers["dlq.source.topic"] == TOPIC
        assert item.headers["dlq.source.partition"] == "0"
        assert item.headers["dlq.source.offset"] == offset
        assert item.headers["dlq.rejected_at_ms"] == str(WHEN_MS)
        assert item.headers["dlq.producer"] == "kafka-delta-ingest"
        assert item.headers["dlq.error"]
    assert broker.published == [
        (item.topic, item.payload, item.key, item.headers) for item in report.dead_lettered
    ]
    assert report.committed == [(TOPIC, 0, 1), (TOPIC, 0, 2), (TOPIC, 0, 3), (TOPIC, 0, 4)]
    assert broker.commits == report.committed
    assert len(report.processed) == 1
    assert report.processed[0]["url"] == "https://example.edu/a"
    assert report.processed[0]["spider_name"] == "discovery"
    assert GOOD not in [item.payload for item in report.dead_lettered]


def test_retry_budget_is_the_configured_limit_and_not_unbounded():
    broker = MockBroker([Delivery(TOPIC, 0, 7, INVALID_JSON), Delivery(TOPIC, 0, 8, GOOD)])
    report = policy.consume_poison_path(broker, dlq_topic="tenant-dlq", max_failures=1, now_ms=lambda: 5)
    assert report.attempts == [1, 1]
    assert report.dead_lettered[0].topic == "tenant-dlq"
    assert report.dead_lettered[0].headers["dlq.source.offset"] == "7"
    assert report.committed == [(TOPIC, 0, 8), (TOPIC, 0, 9)]
    assert report.processed[0]["url"] == "https://example.edu/a"


def test_dlq_publish_failure_does_not_commit():
    broker = MockBroker([Delivery(TOPIC, 0, 0, MALFORMED), Delivery(TOPIC, 0, 1, GOOD)])
    broker.fail_publish = True
    try:
        policy.consume_poison_path(broker, max_failures=1, now_ms=lambda: 1)
    except RuntimeError as exc:
        assert "DLQ produce failed" in str(exc)
    else:
        raise AssertionError("DLQ failure was swallowed")
    assert broker.commits == []
    assert broker.published == []
    assert broker.poll() is broker._log[0]


def test_dlq_error_header_is_truncated_on_a_utf8_boundary():
    rejection = policy.Rejection("schema_validation_failed", "é" * 5000)
    headers = policy.dlq_headers(rejection, TOPIC, 3, 42, WHEN_MS)
    assert len(headers["dlq.error"].encode("utf-8")) <= policy.DLQ_DETAIL_MAX
    assert headers["dlq.error"]
    assert headers["dlq.source.offset"] == "42"


def test_header_keys_and_reasons_match_the_rust_consumer():
    src = MAIN_RS.read_text()
    for reason in ("empty_payload", "parse_failed", "schema_validation_failed", "missing_required_field"):
        assert f'reason: "{reason}"' in src
    for header in (
        "dlq.reason",
        "dlq.error",
        "dlq.source.topic",
        "dlq.source.partition",
        "dlq.source.offset",
        "dlq.rejected_at_ms",
        "dlq.producer",
    ):
        assert f'"{header}"' in src
    assert "fn consume_poison_path" not in src
    assert "async fn ingest" in src
