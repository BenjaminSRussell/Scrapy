"""Consumer-group lag from committed offsets versus the log end (#698).

Lag is how many records a group has not committed: the partition log-end
offset minus the next offset that group will read. A paused consumer stays
on its last commit while the producer offset advances, so the gauge rises
by that same amount. Committing through to the log end brings it back to
zero.

An empty partition, or one whose commit already matches the log end, is
idle and stays at zero. Kafka reports "no commit" as a negative sentinel
(``OFFSET_INVALID`` is -1001); that is not a position, and it must not be
subtracted from the log end or an idle partition looks millions behind.
Two groups on the same topic are sampled separately. One group's commit
does not change the other's series.

The client is duck-typed to ``confluent_kafka.Consumer`` (``assignment``,
``committed``, ``get_watermark_offsets``) so tests can pass a fake and
production can pass a real consumer. This module does not poll or commit.
"""

from __future__ import annotations

from typing import Any

METRIC_NAME = "pipeline_kafka_consumer_lag"
LABELS = ("topic", "partition", "group")

# confluent_kafka / librdkafka sentinels. None of these are a log position.
# OFFSET_INVALID (-1001) is what committed() returns when the group has no offset.
_NO_COMMIT = 0

try:
    from prometheus_client import Gauge

    CONSUMER_LAG = Gauge(
        METRIC_NAME,
        "Records a consumer group has not committed, by topic and partition. "
        "Rises while consumption is paused and the producer offset advances; zero when caught up.",
        list(LABELS),
    )
except Exception:  # prometheus_client missing or the series is already registered
    CONSUMER_LAG = None


def records_lag(log_end: int, committed: int | None, log_start: int = 0) -> int:
    """Non-negative lag for one partition.

    ``committed`` is the next offset the group will read, or a negative
    sentinel / ``None`` when the group has never committed. ``log_end`` is
    the high watermark (next offset the producer will write). ``log_start``
    is the low watermark; records below it are already gone and are not lag.
    """
    end = int(log_end)
    start = int(log_start)
    if end < start:
        return 0
    if committed is None or int(committed) < _NO_COMMIT:
        # No commit yet. An empty log is idle. A non-empty log is all unread.
        return end - start
    pos = int(committed)
    if pos < start:
        pos = start
    if end <= pos:
        return 0
    return end - pos


def _committed_offset(row: Any) -> int | None:
    raw = getattr(row, "offset", None)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def observe_consumer_lag(consumer: Any, group: str, *, timeout: float = 1.0) -> dict[tuple[str, str, str], int]:
    """Sample ``consumer``'s assignment and set ``pipeline_kafka_consumer_lag``.

    Returns ``(topic, partition, group) -> lag`` for the partitions currently
    assigned. Partitions left out of the assignment are not given a series.
    ``group`` is the consumer group id and is the only thing that separates
    two members reading the same topic.
    """
    if not str(group).strip():
        raise ValueError("consumer group id is required")
    assigned = list(consumer.assignment() or [])
    if not assigned:
        return {}
    committed_rows = list(consumer.committed(assigned, timeout=timeout) or [])
    committed_by = {(row.topic, int(row.partition)): _committed_offset(row) for row in committed_rows}
    observed: dict[tuple[str, str, str], int] = {}
    for tp in assigned:
        topic = str(tp.topic)
        partition = int(tp.partition)
        low, high = consumer.get_watermark_offsets(tp, timeout=timeout, cached=False)
        lag = records_lag(int(high), committed_by.get((tp.topic, partition)), int(low))
        key = (topic, str(partition), str(group))
        observed[key] = lag
        if CONSUMER_LAG is not None:
            CONSUMER_LAG.labels(topic=topic, partition=str(partition), group=str(group)).set(lag)
    return observed
