"""Poison-message policy for the Kafka to Delta ingest path (#177).

Consumer entrypoint: ``async fn ingest`` in
``Scraping_project/kafka-delta-ingest/src/main.rs``, started as
``kafka-delta-ingest ingest``. Helm passes ``--dlq-topic`` and
``--max-deserialize-failures`` from ``kafkaDeltaIngestor.config``.

``SchemaValidationPipeline`` (``src/pipelines.py``) publishes producer-side
schema failures to ``validation_failures``. That does not commit the ingest
consumer's offset. A malformed payload that still reaches the ingest topic
is handled by this policy.

After ``max_failures`` deserialize failures of one payload (minimum 1), the
consumer copies the bytes to the DLQ topic and commits the offset. Below that
it does not commit. One bad message therefore cannot block the partition.
The Rust function ``poison_action`` applies the same rule.

``consume_poison_path`` is the same rule against a mocked broker (no Kafka,
no Delta). It is not a second production consumer. ``async fn ingest`` still
reads the topic.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


RETRY = "retry"
DLQ_AND_COMMIT = "dlq_and_commit"


@dataclass(frozen=True)
class PoisonDecision:
    """Outcome for one deserialize failure count, including this attempt."""

    action: str
    commit_offset: bool
    send_to_dlq: bool


def poison_action(failures: int, max_failures: int) -> str:
    """Return ``dlq_and_commit`` once ``failures`` reaches the limit, else ``retry``.

    ``failures`` is 1-based (it includes the attempt being decided). A limit
    below 1 is treated as 1 so a zero config cannot retry without a bound.
    """
    limit = max(1, int(max_failures))
    if int(failures) >= limit:
        return DLQ_AND_COMMIT
    return RETRY


def decide(failures: int, max_failures: int) -> PoisonDecision:
    """Map a failure count to the commit / DLQ side effects."""
    action = poison_action(failures, max_failures)
    if action == DLQ_AND_COMMIT:
        return PoisonDecision(action, commit_offset=True, send_to_dlq=True)
    return PoisonDecision(action, commit_offset=False, send_to_dlq=False)


# Same required fields and timestamp shape as the Rust schema (#531). Kept
# inline so this module stays importable without ``src.utils`` side effects.
REQUIRED_INGEST_FIELDS: tuple[str, ...] = ("url", "scraped_at_utc", "spider_name")
_OPTIONAL_STRING_FIELDS: tuple[str, ...] = ("title", "content", "pipeline_version")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
DEFAULT_DLQ_TOPIC = "scraped-items-dlq"
DLQ_DETAIL_MAX = 4000
DLQ_PRODUCER = "kafka-delta-ingest"


@dataclass(frozen=True)
class Rejection:
    """Why one payload cannot be written. ``reason`` matches the Rust classifier."""

    reason: str
    detail: str


@dataclass(frozen=True)
class Delivery:
    """One broker record. ``payload`` is ``None`` when the message has no value."""

    topic: str
    partition: int
    offset: int
    payload: bytes | None
    key: bytes | None = None


@dataclass(frozen=True)
class DlqPublication:
    """One acked dead-letter produce, including diagnostic headers."""

    topic: str
    payload: bytes
    key: bytes | None
    headers: dict[str, str]


@dataclass
class ConsumeReport:
    """What ``consume_poison_path`` did with a drained broker."""

    processed: list[dict[str, Any]]
    dead_lettered: list[DlqPublication]
    committed: list[tuple[str, int, int]]
    attempts: list[int]


class PoisonBroker(Protocol):
    """In-memory stand-in for the ingest consumer's broker.

    ``poll`` returns the uncommitted head, or ``None`` when caught up. It must
    not advance past a delivery the caller has not ``commit``ted. ``publish``
    waits for the DLQ ack and raises if that ack does not arrive.
    """

    def poll(self) -> Delivery | None: ...

    def publish(self, topic: str, payload: bytes, key: bytes | None, headers: dict[str, str]) -> None: ...

    def commit(self, topic: str, partition: int, next_offset: int) -> None: ...


def _now_ms() -> int:
    return int(time.time() * 1000)


def classify(payload: bytes | None) -> Rejection | None:
    """Validate one payload the way ``fn classify`` does.

    ``None`` means the row may be processed. A rejection is dead-lettered
    after the poison limit, not written.
    """
    if not payload:
        return Rejection("empty_payload", "message has no payload")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        return Rejection("parse_failed", f"invalid utf-8: {exc}")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return Rejection("parse_failed", str(exc))
    if not isinstance(value, dict):
        return Rejection(
            "schema_validation_failed",
            f"expected object, got {type(value).__name__}",
        )
    errors: list[str] = []
    for field in REQUIRED_INGEST_FIELDS:
        if field not in value:
            errors.append(f"required field `{field}` is missing")
        elif not isinstance(value[field], str):
            errors.append(f"`{field}` is not a string")
        elif len(value[field]) < 1:
            errors.append(f"`{field}` is shorter than 1")
    scraped = value.get("scraped_at_utc")
    if isinstance(scraped, str) and scraped and _TIMESTAMP.match(scraped) is None:
        errors.append("`scraped_at_utc` does not match the timestamp pattern")
    for field in _OPTIONAL_STRING_FIELDS:
        if field in value and value[field] is not None and not isinstance(value[field], str):
            errors.append(f"`{field}` is not a string or null")
    if errors:
        return Rejection("schema_validation_failed", "; ".join(errors))
    for field in REQUIRED_INGEST_FIELDS:
        raw = value[field]
        if isinstance(raw, str) and not raw.strip():
            return Rejection("missing_required_field", f"required field `{field}` is empty")
    return None


def _truncate_detail(detail: str, limit: int = DLQ_DETAIL_MAX) -> str:
    encoded = detail.encode("utf-8")
    if len(encoded) <= limit:
        return detail
    return encoded[:limit].decode("utf-8", errors="ignore")


def dlq_headers(
    rejection: Rejection,
    source_topic: str,
    partition: int,
    offset: int,
    rejected_at_ms: int,
) -> dict[str, str]:
    """Diagnostic headers on a dead-lettered payload. Same keys as ``dlq_headers`` in Rust."""
    return {
        "dlq.reason": rejection.reason,
        "dlq.error": _truncate_detail(rejection.detail),
        "dlq.source.topic": source_topic,
        "dlq.source.partition": str(partition),
        "dlq.source.offset": str(offset),
        "dlq.rejected_at_ms": str(rejected_at_ms),
        "dlq.producer": DLQ_PRODUCER,
    }


def consume_poison_path(
    broker: PoisonBroker,
    *,
    dlq_topic: str = DEFAULT_DLQ_TOPIC,
    max_failures: int = 3,
    now_ms: Callable[[], int] | None = None,
    max_deliveries: int = 10_000,
) -> ConsumeReport:
    """Drain ``broker`` and isolate poison payloads onto ``dlq_topic``.

    Each delivery is classified up to the poison limit. A failure below the
    limit does not publish and does not commit, and the next offset is not
    read. At the limit the original bytes are published with diagnostic
    headers and the next offset is committed. A payload that classifies is
    processed and its offset is committed, so a following valid message is
    then eligible.

    If the DLQ publish raises, the offset is not committed and the error
    propagates. The payload is not dropped.
    """
    clock = now_ms or _now_ms
    limit = max(1, int(max_failures))
    processed: list[dict[str, Any]] = []
    dead_lettered: list[DlqPublication] = []
    committed: list[tuple[str, int, int]] = []
    attempts_per: list[int] = []
    seen = 0
    while True:
        delivery = broker.poll()
        if delivery is None:
            break
        seen += 1
        if seen > max_deliveries:
            raise RuntimeError("consumer did not advance past a delivery")
        payload = delivery.payload
        attempts = 0
        while True:
            attempts += 1
            if attempts > limit:
                raise RuntimeError(
                    f"deserialize retries were not bounded at {delivery.topic}"
                    f"[{delivery.partition}]@{delivery.offset}"
                )
            rejection = classify(payload)
            if rejection is None:
                if payload is None:
                    raise RuntimeError("classified an empty payload as accepted")
                broker.commit(delivery.topic, delivery.partition, delivery.offset + 1)
                committed.append((delivery.topic, delivery.partition, delivery.offset + 1))
                processed.append(json.loads(payload.decode("utf-8")))
                attempts_per.append(attempts)
                break
            decision = decide(attempts, max_failures)
            if decision.action == RETRY:
                continue
            headers = dlq_headers(
                rejection,
                delivery.topic,
                delivery.partition,
                delivery.offset,
                clock(),
            )
            body = payload if payload is not None else b""
            broker.publish(dlq_topic, body, delivery.key, headers)
            broker.commit(delivery.topic, delivery.partition, delivery.offset + 1)
            dead_lettered.append(
                DlqPublication(topic=dlq_topic, payload=body, key=delivery.key, headers=headers)
            )
            committed.append((delivery.topic, delivery.partition, delivery.offset + 1))
            attempts_per.append(attempts)
            break
    return ConsumeReport(
        processed=processed,
        dead_lettered=dead_lettered,
        committed=committed,
        attempts=attempts_per,
    )
