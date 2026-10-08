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

Enforcement (#464): with ``KAFKA_REQUIRE_IDEMPOTENCE=true`` (set in the Helm
application configmap) a producer whose final config is not
idempotent (``acks`` other than all, ``enable.idempotence`` off, or more than 5
in-flight requests) refuses to start instead of silently allowing duplicates
and reordering on retry.

Semantics: the idempotent producer gives exactly-once, in-order writes *per
partition per producer session*. Across producer restarts, and on the consumer
side, delivery is at-least-once: kafka-delta-ingest commits offsets only after
its Delta commit, so a crash between the two replays messages, and downstream
tables dedupe by ``url_hash``. Messages are keyed by ``url_hash`` (#285), so
every version of a URL lands on the same partition, in order.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

DEFAULT_ACKS = "all"
DEFAULT_MESSAGE_KEY_FIELD = "url_hash"
MAX_IDEMPOTENT_IN_FLIGHT = 5
_TRUE = {"1", "true", "yes", "on"}


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


def idempotence_required(value: Any = None) -> bool:
    """``KAFKA_REQUIRE_IDEMPOTENCE`` (or an explicit setting) as a bool."""
    raw = os.getenv("KAFKA_REQUIRE_IDEMPOTENCE", "") if value is None else value
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in _TRUE


def idempotence_violations(config: Mapping[str, Any]) -> list[str]:
    """Why a final librdkafka producer config is not idempotent (empty list = OK)."""
    problems = []
    acks = str(config.get("acks", "")).strip().lower()
    if acks not in {"all", "-1"}:
        problems.append(f"acks={config.get('acks')!r} (needs all)")
    if not idempotence_required(config.get("enable.idempotence", False)):
        problems.append("enable.idempotence is not true")
    in_flight = config.get("max.in.flight.requests.per.connection", MAX_IDEMPOTENT_IN_FLIGHT)
    try:
        if int(in_flight) > MAX_IDEMPOTENT_IN_FLIGHT:
            problems.append(f"max.in.flight.requests.per.connection={in_flight} (max {MAX_IDEMPOTENT_IN_FLIGHT})")
    except (TypeError, ValueError):
        problems.append(f"max.in.flight.requests.per.connection={in_flight!r} is not an integer")
    return problems


def enforce_idempotent_producer(config: Mapping[str, Any]) -> None:
    """Raise ValueError if ``config`` would allow duplicate/reordered retries (#464)."""
    problems = idempotence_violations(config)
    if problems:
        raise ValueError(
            "KAFKA_REQUIRE_IDEMPOTENCE is set but the producer config is not idempotent: "
            + "; ".join(problems)
        )


def message_key(record: Mapping[str, Any], field: str | None = DEFAULT_MESSAGE_KEY_FIELD) -> bytes | None:
    """Deterministic partition key for a record (#285).

    Uses ``record[field]`` when it is a non-empty string. If the field is
    ``url_hash`` and missing, derives it from ``url`` with the same hasher the
    lake uses (``seed_manager.default_url_hasher``); for any other field it
    falls back to the URL itself. Returns None (unkeyed) only when neither is
    available, or when keying is disabled with an empty field name.
    """
    if not field:
        return None
    value = record.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip().encode("utf-8")
    if value is not None and not isinstance(value, str):
        return str(value).encode("utf-8")
    url = record.get("url")
    if not isinstance(url, str) or not url.strip():
        return None
    if field == "url_hash":
        from src.lakehouse.seed_manager import default_url_hasher

        return default_url_hasher(url.strip()).encode("utf-8")
    return url.strip().encode("utf-8")
