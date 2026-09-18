"""Scout queue-routing item helpers (#608)."""
from __future__ import annotations

from typing import Any


def is_queue_routing_item(item: Any) -> bool:
    """True for Scout queue handoff dicts (Stage2 / JS spider).

    These plain dicts carry ``target_stage`` / ``target_spider`` and must not be
    DropItem'd by SchemaValidation before QueueItemPipeline (priority 350) runs.
    """
    if not isinstance(item, dict):
        return False
    return bool(item.get("target_stage") or item.get("target_spider"))
