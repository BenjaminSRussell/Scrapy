"""#698: consumer lag rises when consumption stalls and the producer moves ahead.

No broker. A fake client reports assignment, committed offsets, and
watermarks. Pausing a group freezes its commits while ``produce`` advances
the shared log end.
"""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

from src.utils.kafka_consumer_lag import LABELS, METRIC_NAME, observe_consumer_lag, records_lag

TOPIC = "validated_items"


class _TP:
    def __init__(self, topic: str, partition: int, offset: int = -1001) -> None:
        self.topic = topic
        self.partition = partition
        self.offset = offset


class MockConsumer:
    """One consumer group. Log watermarks are shared; commits are not."""

    def __init__(self, group: str, log: dict[tuple[str, int], list[int]], parts: list[tuple[str, int]]) -> None:
        self.group = group
        self.log = log
        self.paused = False
        self._parts = [_TP(topic, partition) for topic, partition in parts]
        self.committed_at: dict[tuple[str, int], int] = {}

    def assignment(self) -> list[_TP]:
        return list(self._parts)

    def committed(self, partitions: list[_TP], timeout: float | None = None) -> list[_TP]:
        return [
            _TP(tp.topic, tp.partition, self.committed_at.get((tp.topic, tp.partition), -1001))
            for tp in partitions
        ]

    def get_watermark_offsets(
        self, partition: _TP, timeout: float | None = None, cached: bool = False
    ) -> tuple[int, int]:
        low, high = self.log[(partition.topic, partition.partition)]
        return low, high

    def commit_through(self, topic: str, partition: int) -> None:
        """Commit up to the log end. A paused group does not move."""
        if self.paused:
            return
        _low, high = self.log[(topic, partition)]
        self.committed_at[(topic, partition)] = high


def _produce(log: dict[tuple[str, int], list[int]], topic: str, partition: int, count: int) -> None:
    low, high = log[(topic, partition)]
    log[(topic, partition)] = [low, high + count]


def _gauge(topic: str, partition: str, group: str) -> float | None:
    return REGISTRY.get_sample_value(
        METRIC_NAME,
        {"topic": topic, "partition": partition, "group": group},
    )


def test_records_lag_math_and_idle_sentinels():
    assert records_lag(10, 10) == 0
    assert records_lag(18, 10) == 8
    assert records_lag(10, 12) == 0  # commit ahead of the log is not negative lag
    # OFFSET_INVALID on an empty partition is idle, not 1001 records of lag.
    assert records_lag(0, -1001, 0) == 0
    assert records_lag(0, None, 0) == 0
    # No commit, but the producer has written: the whole log is unread.
    assert records_lag(5, -1001, 0) == 5
    # Retention moved the start past an old commit. Gone records are not lag.
    assert records_lag(20, 4, log_start=12) == 8


def test_paused_consumer_lag_tracks_the_producer_then_recovers():
    log = {(TOPIC, 0): [0, 10], (TOPIC, 1): [0, 4]}
    stalled = MockConsumer("ingest-a", log, [(TOPIC, 0), (TOPIC, 1)])
    other = MockConsumer("ingest-b", log, [(TOPIC, 0), (TOPIC, 1)])
    for client, end0, end1 in ((stalled, 10, 4), (other, 10, 4)):
        client.committed_at[(TOPIC, 0)] = end0
        client.committed_at[(TOPIC, 1)] = end1

    caught_up = observe_consumer_lag(stalled, stalled.group)
    observe_consumer_lag(other, other.group)
    assert caught_up[(TOPIC, "0", "ingest-a")] == 0
    assert caught_up[(TOPIC, "1", "ingest-a")] == 0
    assert _gauge(TOPIC, "0", "ingest-a") == 0.0
    assert _gauge(TOPIC, "0", "ingest-b") == 0.0

    stalled.paused = True
    other.paused = True
    _produce(log, TOPIC, 0, 7)  # partition 1 is idle: its log end does not move

    first = observe_consumer_lag(stalled, "ingest-a")
    assert first[(TOPIC, "0", "ingest-a")] == 7
    assert first[(TOPIC, "1", "ingest-a")] == 0
    assert _gauge(TOPIC, "0", "ingest-a") == 7.0
    assert _gauge(TOPIC, "1", "ingest-a") == 0.0
    # The other group has not been sampled again, so its series stays at zero
    # until its own client is read. Sampling A must not write B's labels.
    assert _gauge(TOPIC, "0", "ingest-b") == 0.0

    _produce(log, TOPIC, 0, 3)
    second = observe_consumer_lag(stalled, "ingest-a")
    assert second[(TOPIC, "0", "ingest-a")] == 10  # 7 + 3, deterministic
    assert second[(TOPIC, "1", "ingest-a")] == 0
    assert _gauge(TOPIC, "0", "ingest-a") == 10.0

    behind = observe_consumer_lag(other, "ingest-b")
    assert behind[(TOPIC, "0", "ingest-b")] == 10
    assert behind[(TOPIC, "1", "ingest-b")] == 0
    assert _gauge(TOPIC, "0", "ingest-b") == 10.0

    stalled.paused = False
    stalled.commit_through(TOPIC, 0)
    stalled.commit_through(TOPIC, 1)
    recovered = observe_consumer_lag(stalled, "ingest-a")
    assert recovered[(TOPIC, "0", "ingest-a")] == 0
    assert recovered[(TOPIC, "1", "ingest-a")] == 0
    assert _gauge(TOPIC, "0", "ingest-a") == 0.0
    # B is still paused. A's commit must not clear B's lag.
    assert _gauge(TOPIC, "0", "ingest-b") == 10.0
    still = observe_consumer_lag(other, "ingest-b")
    assert still[(TOPIC, "0", "ingest-b")] == 10
    assert still[(TOPIC, "1", "ingest-b")] == 0

    # A partition nobody is assigned does not grow a series.
    log[(TOPIC, 2)] = [0, 50]
    assert _gauge(TOPIC, "2", "ingest-a") is None
    assert _gauge(TOPIC, "2", "ingest-b") is None


def test_metric_labels_are_topic_partition_and_group():
    from src.utils.kafka_consumer_lag import CONSUMER_LAG

    assert CONSUMER_LAG is not None
    assert tuple(CONSUMER_LAG._labelnames) == LABELS
    log = {(TOPIC, 0): [0, 3]}
    client = MockConsumer("zsc-service", log, [(TOPIC, 0)])
    client.paused = True  # never committed; producer is already ahead
    observed = observe_consumer_lag(client, "zsc-service")
    assert observed == {(TOPIC, "0", "zsc-service"): 3}
    assert _gauge(TOPIC, "0", "zsc-service") == 3.0
    wanted = {"topic": TOPIC, "partition": "0", "group": "zsc-service"}
    matched = [
        sample
        for metric in REGISTRY.collect()
        if metric.name == METRIC_NAME
        for sample in metric.samples
        if sample.name == METRIC_NAME and {k: sample.labels.get(k) for k in LABELS} == wanted
    ]
    assert len(matched) == 1
    assert matched[0].value == 3.0


def test_empty_group_is_rejected():
    client = MockConsumer("ingest-a", {(TOPIC, 0): [0, 0]}, [(TOPIC, 0)])
    with pytest.raises(ValueError):
        observe_consumer_lag(client, "  ")
