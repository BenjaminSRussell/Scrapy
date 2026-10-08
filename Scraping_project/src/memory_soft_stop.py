"""Graceful drain before the cgroup OOM killer (#539).

When the kernel OOM-kills the crawler, the Twisted reactor never runs
``close_spider``: the Kafka producer queue and batched queue rows vanish.
Scrapy's own MEMUSAGE extension doesn't prevent this: it compares *peak* RSS
(``ru_maxrss``) with a fixed ``MEMUSAGE_LIMIT_MB`` (4096) that has nothing to
do with the container limit (2G in compose), so the OOM killer fires first.

``MemorySoftStop`` reads the container's real memory limit and usage from
cgroup v2 (``memory.max`` / ``memory.current``) or v1
(``memory.limit_in_bytes`` / ``memory.usage_in_bytes``), falling back to
process RSS. When usage crosses ``MEMORY_SOFT_STOP_FRACTION`` (0.85) of the
limit it calls ``engine.close_spider(spider, "memory_soft_stop")``. That is
Scrapy's graceful path: no new requests are scheduled, in-flight responses
finish, and every pipeline's ``close_spider`` runs. The Kafka pipeline flushes
and spills anything undelivered to its fsync'd spill file (#249), and queue
pipelines flush their batches.

Settings: ``MEMORY_SOFT_STOP_ENABLED`` (True), ``MEMORY_SOFT_STOP_FRACTION``
(0.85), ``MEMORY_SOFT_STOP_INTERVAL`` (5s), ``MEMORY_SOFT_STOP_LIMIT_MB``
(override when no cgroup limit is visible; 0 = none).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

CGROUP_ROOT = Path("/sys/fs/cgroup")
# cgroup v1 reports "unlimited" as a huge page-aligned number.
_V1_UNLIMITED = 1 << 60

try:  # pragma: no cover - metrics optional
    from prometheus_client import Counter, Gauge

    MEMORY_SOFT_STOPS: Optional[Counter] = Counter(
        "scrapy_memory_soft_stop_total",
        "Crawls drained gracefully because memory crossed the soft limit (#539).",
    )
    MEMORY_USAGE_RATIO: Optional[Gauge] = Gauge(
        "scrapy_memory_usage_ratio",
        "Crawler memory usage / container memory limit (#539).",
    )
except Exception:  # pragma: no cover
    MEMORY_SOFT_STOPS = MEMORY_USAGE_RATIO = None


def _read_int(path: Path) -> Optional[int]:
    try:
        raw = path.read_text().strip()
    except OSError:
        return None
    if raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def cgroup_limit_bytes(root: Path = CGROUP_ROOT) -> Optional[int]:
    v2 = root / "memory.max"
    if v2.exists():
        return _read_int(v2)
    v1 = _read_int(root / "memory" / "memory.limit_in_bytes")
    if v1 is None or v1 >= _V1_UNLIMITED:
        return None
    return v1


def _process_rss_bytes() -> Optional[int]:
    try:
        pages = int(Path("/proc/self/statm").read_text().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


def usage_bytes(root: Path = CGROUP_ROOT) -> Optional[int]:
    for path in (root / "memory.current", root / "memory" / "memory.usage_in_bytes"):
        value = _read_int(path)
        if value is not None:
            return value
    return _process_rss_bytes()


class MemorySoftStop:
    def __init__(self, crawler: Any, fraction: float = 0.85, interval: float = 5.0,
                 limit_override_mb: int = 0, root: Path = CGROUP_ROOT):
        self.crawler = crawler
        self.fraction = fraction
        self.interval = interval
        self.root = root
        self.limit = cgroup_limit_bytes(root) or (limit_override_mb * 1024 * 1024 or None)
        self.triggered = False
        self._task: Any = None
        self._spider: Any = None

    @classmethod
    def from_crawler(cls, crawler: Any) -> "MemorySoftStop":
        from scrapy import signals
        from scrapy.exceptions import NotConfigured

        s = crawler.settings
        if not s.getbool("MEMORY_SOFT_STOP_ENABLED", True):
            raise NotConfigured("MEMORY_SOFT_STOP_ENABLED is off")
        ext = cls(
            crawler,
            fraction=s.getfloat("MEMORY_SOFT_STOP_FRACTION", 0.85),
            interval=s.getfloat("MEMORY_SOFT_STOP_INTERVAL", 5.0),
            limit_override_mb=s.getint("MEMORY_SOFT_STOP_LIMIT_MB", 0),
            root=Path(s.get("MEMORY_SOFT_STOP_CGROUP_ROOT") or CGROUP_ROOT),
        )
        if not ext.limit:
            raise NotConfigured("no container memory limit visible; set MEMORY_SOFT_STOP_LIMIT_MB")
        crawler.signals.connect(ext.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(ext.spider_closed, signal=signals.spider_closed)
        return ext

    @property
    def soft_limit(self) -> int:
        return int((self.limit or 0) * self.fraction)

    def spider_opened(self, spider: Any) -> None:
        from twisted.internet import task

        self._spider = spider
        logger.info(
            f"[memory] soft stop at {self.soft_limit / 2**20:.0f}MiB "
            f"({self.fraction:.0%} of {self.limit / 2**20:.0f}MiB container limit)"
        )
        self._task = task.LoopingCall(self.check)
        self._task.start(self.interval, now=True)

    def spider_closed(self, spider: Any) -> None:
        if self._task is not None and self._task.running:
            self._task.stop()

    def check(self) -> bool:
        """Returns True if this check triggered the soft stop."""
        used = usage_bytes(self.root)
        if used is None or not self.limit:
            return False
        if MEMORY_USAGE_RATIO is not None:
            MEMORY_USAGE_RATIO.set(used / self.limit)
        if self.triggered or used < self.soft_limit:
            return False
        self.triggered = True
        logger.error(
            f"[memory] usage {used / 2**20:.0f}MiB >= soft limit {self.soft_limit / 2**20:.0f}MiB; "
            "draining crawl (pipelines flush/spill) before the OOM killer"
        )
        if MEMORY_SOFT_STOPS is not None:
            MEMORY_SOFT_STOPS.inc()
        stats = getattr(self.crawler, "stats", None)
        if stats is not None:
            stats.set_value("memory_soft_stop/triggered", 1)
            stats.set_value("memory_soft_stop/usage_bytes", used)
        engine = getattr(self.crawler, "engine", None)
        if engine is not None and self._spider is not None:
            engine.close_spider(self._spider, "memory_soft_stop")
        return True
