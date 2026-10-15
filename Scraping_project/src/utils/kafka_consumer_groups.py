"""Per-role Kafka consumer groups (#624).

``ingest``, ``dq``, and ``replay`` are incompatible processors. They must not
share a ``group.id``: a replay or a data-quality pass that joined the ingest
group would move the lake cursor. The legacy ``kafka.consumer.group_id`` is
the retired shared id and is not a role.

Lag recording rules derive the ``group`` label from ``client.id`` by stripping
the last hyphen segment (``scrapy-ingestor-123`` -> ``scrapy-ingestor``).
``client_id_for_group`` builds that shape so each role alerts on its own group.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

ROLES = ("ingest", "dq", "replay")


class SharedConsumerGroupError(ValueError):
    """Two incompatible processors were given the same group.id."""


def _group_id(role: str, value: object) -> str:
    if not isinstance(value, str) or value == "" or any(ch.isspace() for ch in value):
        raise SharedConsumerGroupError(
            f"kafka.consumer.groups.{role} must be a non-empty group id with no whitespace"
        )
    return value


def require_distinct_groups(
    groups: Mapping[str, object],
    *,
    legacy_group_id: str | None = None,
) -> dict[str, str]:
    """Return ``{role: group.id}`` or raise when roles are missing or shared.

    ``legacy_group_id`` is the retired shared id (``kafka.consumer.group_id``).
    A role that reuses it is rejected so a new processor cannot join whatever
    still sits on that cursor.
    """
    if not isinstance(groups, Mapping):
        raise SharedConsumerGroupError("kafka.consumer.groups must be a mapping of role to group id")
    unknown = sorted(set(groups) - set(ROLES))
    if unknown:
        raise SharedConsumerGroupError(
            "unknown consumer role(s): " + ", ".join(unknown) + f"; expected {', '.join(ROLES)}"
        )
    missing = [role for role in ROLES if role not in groups]
    if missing:
        raise SharedConsumerGroupError(
            "kafka.consumer.groups missing " + ", ".join(missing)
        )
    resolved = {role: _group_id(role, groups[role]) for role in ROLES}
    seen: dict[str, str] = {}
    for role, group_id in resolved.items():
        other = seen.get(group_id)
        if other is not None:
            raise SharedConsumerGroupError(
                f"consumer group {group_id!r} is shared by {other} and {role}; "
                "incompatible processors must not share a group.id"
            )
        seen[group_id] = role
    if legacy_group_id is not None:
        if not isinstance(legacy_group_id, str):
            raise SharedConsumerGroupError("kafka.consumer.group_id must be a string")
        for role, group_id in resolved.items():
            if group_id == legacy_group_id:
                raise SharedConsumerGroupError(
                    f"kafka.consumer.groups.{role} reuses retired shared group {legacy_group_id!r}; "
                    "give this role its own group.id"
                )
    return resolved


def group_for_role(
    role: str,
    groups: Mapping[str, object],
    *,
    legacy_group_id: str | None = None,
) -> str:
    """Group id for one role, after the distinct-group check."""
    if role not in ROLES:
        raise SharedConsumerGroupError(
            f"unknown consumer role {role!r}; expected one of {', '.join(ROLES)}"
        )
    return require_distinct_groups(groups, legacy_group_id=legacy_group_id)[role]


def client_id_for_group(group_id: str, instance: str | None = None) -> str:
    """``client.id`` whose lag ``group`` label equals ``group_id``.

    The suffix is one hyphen-free segment (the process id by default). A hyphen
    in the suffix would be stripped as the identifier and the label would no
    longer match ``group.id``.
    """
    if not isinstance(group_id, str) or group_id == "" or any(ch.isspace() for ch in group_id):
        raise SharedConsumerGroupError("group id must be non-empty and have no whitespace")
    suffix = str(os.getpid()) if instance is None else instance
    if not isinstance(suffix, str) or suffix == "" or "-" in suffix or any(ch.isspace() for ch in suffix):
        raise SharedConsumerGroupError("client.id suffix must be one hyphen-free segment")
    return f"{group_id}-{suffix}"


def librdkafka_config_for_role(
    role: str,
    groups: Mapping[str, object],
    *,
    bootstrap_servers: str,
    legacy_group_id: str | None = None,
    instance: str | None = None,
    auto_offset_reset: str = "earliest",
) -> dict[str, Any]:
    """Consumer config for one role. ``group.id`` is not overridable here.

    Auto-commit stays off so a DQ or replay consumer does not commit on poll.
    Callers choose ``auto_offset_reset`` (replay usually wants ``earliest``)
    but cannot point this role at another role's group.
    """
    group_id = group_for_role(role, groups, legacy_group_id=legacy_group_id)
    if not isinstance(bootstrap_servers, str) or bootstrap_servers == "":
        raise SharedConsumerGroupError("bootstrap.servers is required")
    if auto_offset_reset not in {"earliest", "latest"}:
        raise SharedConsumerGroupError("auto.offset.reset must be earliest or latest")
    return {
        "bootstrap.servers": bootstrap_servers,
        "group.id": group_id,
        "client.id": client_id_for_group(group_id, instance),
        "auto.offset.reset": auto_offset_reset,
        "enable.auto.commit": False,
    }
