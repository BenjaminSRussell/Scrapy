"""Bounded stage-1 → stage-2 handoff (#703).

Stage 1 can produce faster than stage 2 consumes. ``StageQueueHandoff`` keeps
the in-memory queue at or under the Redis high watermark (the same resolver as
``QueueBacklog``, default 10000) and blocks the producer, via ``admit_enqueue``,
until stage 2 drains to the low watermark. A refused put is not stored. Each
key is queued and delivered at most once, so a retry after stage 2 catches up
does not duplicate work.

The block sleeper may call ``get`` (a slow stage-2 worker). That callback runs
on the producer thread; do not call ``put`` from it.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Any

from src.utils.redis import admit_enqueue, queue_watermark_settings


class StageQueueHandoff:
    """In-memory stage-2 queue with producer blocking at the high watermark."""

    def __init__(
        self,
        name: str = "stage2_queue",
        *,
        sleeper: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.name = name
        self.sleeper = sleeper
        self.clock = clock
        self._items: deque[tuple[str, Any]] = deque()
        self._queued: set[str] = set()
        self._delivered: set[str] = set()
        self.peak = 0
        self.block_events = 0

    def qsize(self) -> int:
        return len(self._items)

    def put(self, key: str, item: Any) -> bool:
        """Queue ``item`` under ``key``.

        Returns True if the key is accepted (newly queued, already queued, or
        already delivered). Returns False when the queue stayed above the low
        watermark until the block budget ran out; the item is not retained.
        """
        while True:
            if key in self._delivered or key in self._queued:
                return True
            high = int(queue_watermark_settings()["high"])

            def probe(high: int = high) -> int:
                size = self.qsize()
                # Full is strictly over the watermark, so the gate blocks
                # instead of admitting one more row than the cap.
                return high + 1 if size >= high else size

            if not admit_enqueue(
                self.name,
                probe(),
                priority=100,
                depth_probe=probe,
                sleeper=self._block,
                clock=self.clock,
            ):
                return False
            if key in self._delivered or key in self._queued:
                return True
            if self.qsize() < high:
                self._items.append((key, item))
                self._queued.add(key)
                if self.qsize() > self.peak:
                    self.peak = self.qsize()
                return True

    def get(self) -> Any:
        """Pop the next undelivered item, or None when the queue is empty."""
        while self._items:
            key, item = self._items.popleft()
            self._queued.discard(key)
            if key in self._delivered:
                continue
            self._delivered.add(key)
            return item
        return None

    def _block(self, seconds: float) -> None:
        self.block_events += 1
        if self.sleeper is not None:
            self.sleeper(seconds)
        else:
            time.sleep(seconds)
