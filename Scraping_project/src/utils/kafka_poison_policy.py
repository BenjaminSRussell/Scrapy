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
"""

from __future__ import annotations

from dataclasses import dataclass


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
