"""Producer durability defaults shared by every Kafka producer (#174).

Tradeoff: ``acks=all`` waits until every in-sync replica has the record, so a
leader failover cannot drop acknowledged messages. ``acks=1`` only waits for
the leader and can lose whatever it had not yet replicated. ``acks=all`` costs
one extra replication round-trip per batch; with ``linger.ms`` batching that is
negligible for this pipeline.

Durability also needs the broker side: in multi-broker production run topics
with replication factor >= 3 and ``min.insync.replicas=2`` (Helm values
``kafka.config.defaultReplicationFactor`` / ``minInsyncReplicas``). On the
bundled single-broker deployment ``acks=all`` behaves like ``acks=1`` (the ISR
is just the leader) and costs nothing.

``enable.idempotence`` is turned on with ``acks=all`` so producer retries do
not duplicate or reorder messages.

Override with ``KAFKA_PRODUCER_ACKS`` (``all``/``-1``/``1``/``0``), or per key
in ``config.yml`` ``kafka.producer``.
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_ACKS = "all"


def producer_durability_config(acks: str | int | None = None) -> dict[str, Any]:
    value = str(acks if acks is not None else os.getenv("KAFKA_PRODUCER_ACKS", DEFAULT_ACKS)).strip().lower()
    if value == "-1":
        value = "all"
    if value not in {"all", "1", "0"}:
        raise ValueError(f"Invalid Kafka acks {value!r}; expected all, -1, 1 or 0")
    config: dict[str, Any] = {"acks": value}
    if value == "all":
        config["enable.idempotence"] = True
    return config
