"""Kafka topic contract: every topic the pipeline produces to, with its settings (#410).

``config.yml`` ``kafka.topics`` maps a logical name to the Kafka topic name, and
``kafka.topic_settings`` gives each logical topic its partitions and retention.
``SchemaValidationPipeline`` publishes to ``kafka.topics.validation_failures``;
before #410 that topic was in neither section, so it only existed if the broker
auto-created it with broker defaults (or the messages were lost).

``ensure_topics`` creates missing topics and leaves existing ones alone (it
never changes partitions or retention of a live topic). Run it at deploy time::

    python -m src.utils.kafka_topics            # create missing topics
    python -m src.utils.kafka_topics --dry-run  # print the plan only

The Helm chart runs the same command as a post-install/upgrade hook
(``templates/kafka-topics-job.yaml``).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Mapping

logger = logging.getLogger(__name__)

# Logical topics every deployment needs, and the producer of each.
REQUIRED_TOPICS = {
    "scraped_items": "KafkaPipeline (Stage 1 items for kafka-delta-ingest)",
    "dead_letter": "dead-letter queue for invalid messages",
    "validation_failures": "SchemaValidationPipeline (ValidationFailureRecord JSON)",
}

DEFAULT_TOPIC_NAMES = {
    "scraped_items": "scraped-items",
    "dead_letter": "scraped-items-dlq",
    "validation_failures": "validation_failures",
}


@dataclass(frozen=True)
class TopicSpec:
    logical: str
    name: str
    partitions: int = -1  # -1: broker default (num.partitions)
    replication_factor: int = -1  # -1: broker default (default.replication.factor)
    config: dict[str, str] = field(default_factory=dict)


def topic_name(cfg: Any, logical: str) -> str:
    """Kafka topic name for a logical topic, from ``kafka.topics``."""
    value = cfg.get(f"kafka.topics.{logical}") if cfg is not None else None
    return str(value or DEFAULT_TOPIC_NAMES[logical])


def topic_plan(cfg: Any) -> list[TopicSpec]:
    """One spec per ``kafka.topics`` entry, with ``kafka.topic_settings`` applied."""
    topics: dict[str, Any] = dict(DEFAULT_TOPIC_NAMES)
    topics.update(cfg.get("kafka.topics") or {})
    settings: Mapping[str, Any] = cfg.get("kafka.topic_settings") or {}
    specs = []
    for logical, name in topics.items():
        s = dict(settings.get(logical) or {})
        conf = {}
        if s.get("retention_ms") is not None:
            conf["retention.ms"] = str(int(s["retention_ms"]))
        if s.get("cleanup_policy"):
            conf["cleanup.policy"] = str(s["cleanup_policy"])
        specs.append(TopicSpec(
            logical=logical,
            name=str(name),
            partitions=int(s.get("partitions", -1)),
            replication_factor=int(s.get("replication_factor", -1)),
            config=conf,
        ))
    return specs


def admin_config_from_env() -> dict[str, str]:
    """AdminClient settings from the same env vars the producers use."""
    conf = {"bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")}
    for env, key in (("KAFKA_SECURITY_PROTOCOL", "security.protocol"),
                     ("KAFKA_SASL_MECHANISM", "sasl.mechanism"),
                     ("KAFKA_SASL_USERNAME", "sasl.username"),
                     ("KAFKA_SASL_PASSWORD", "sasl.password")):
        if os.getenv(env):
            conf[key] = os.environ[env]
    return conf


def ensure_topics(admin: Any, specs: list[TopicSpec], timeout: float = 30.0) -> dict[str, str]:
    """Create the topics in ``specs`` that do not exist yet.

    Returns ``{topic: "exists" | "created" | "error: ..."}``. Existing topics
    are never altered.
    """
    from confluent_kafka.admin import NewTopic

    existing = set(admin.list_topics(timeout=timeout).topics)
    result = {s.name: "exists" for s in specs if s.name in existing}
    missing = [s for s in specs if s.name not in existing]
    if not missing:
        return result
    futures = admin.create_topics(
        [NewTopic(s.name, num_partitions=s.partitions, replication_factor=s.replication_factor,
                  config=s.config) for s in missing],
        operation_timeout=timeout,
    )
    for name, fut in futures.items():
        try:
            fut.result()
            result[name] = "created"
        except Exception as exc:  # noqa: BLE001 - reported per topic
            # Another process may have created it between list and create.
            if "TOPIC_ALREADY_EXISTS" in str(exc):
                result[name] = "exists"
            else:
                result[name] = f"error: {exc}"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the Kafka topics config.yml declares (#410).")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, do not connect")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from src.core.config import get_config

    specs = topic_plan(get_config())
    for s in specs:
        logger.info("%-20s -> %-22s partitions=%s replication=%s %s", s.logical, s.name,
                    s.partitions, s.replication_factor, s.config)
    if args.dry_run:
        return 0

    from confluent_kafka.admin import AdminClient

    result = ensure_topics(AdminClient(admin_config_from_env()), specs, timeout=args.timeout)
    for name, status in sorted(result.items()):
        logger.info("%s: %s", name, status)
    return 1 if any(v.startswith("error") for v in result.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
