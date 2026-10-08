"""Disk quota for Scrapy's HTTP cache (#496).

``HTTPCACHE_DIR`` can grow until it fills the PVC on long crawls. This
extension measures the cache, exports the size, and when
``HTTPCACHE_MAX_BYTES`` is set it deletes the oldest cached responses until
usage drops to ``HTTPCACHE_PRUNE_TARGET_RATIO`` of the quota. It runs at
spider open, every ``HTTPCACHE_PRUNE_INTERVAL_SECS`` seconds, and at spider
close.

Pruning works per entry on ``FilesystemCacheStorage`` (the default since
#496): one directory per cached request with a ``pickled_meta`` marker. A DBM
cache is one opaque file that can't be shrunk while it is open. It is counted
toward usage and a warning is logged, but it is never deleted. Pruning never
raises: a failure is logged and counted, and the crawl continues.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scrapy import signals
from scrapy.exceptions import NotConfigured
from scrapy.utils.project import data_path

logger = logging.getLogger(__name__)

ENTRY_MARKER = "pickled_meta"

try:
    from prometheus_client import Counter, Gauge

    HTTPCACHE_BYTES: Any = Gauge("scrapy_httpcache_bytes", "Bytes used by the Scrapy HTTP cache directory")
    HTTPCACHE_QUOTA_BYTES: Any = Gauge(
        "scrapy_httpcache_quota_bytes", "Configured HTTPCACHE_MAX_BYTES (0 = no quota)"
    )
    HTTPCACHE_PRUNED: Any = Counter(
        "scrapy_httpcache_pruned_entries_total", "Cached responses deleted to stay under the quota"
    )
    HTTPCACHE_PRUNE_ERRORS: Any = Counter(
        "scrapy_httpcache_prune_errors_total", "Cache entries that could not be measured or deleted"
    )
except Exception:  # prometheus_client missing or already registered
    HTTPCACHE_BYTES = HTTPCACHE_QUOTA_BYTES = HTTPCACHE_PRUNED = HTTPCACHE_PRUNE_ERRORS = None


def _inc(metric: Any, n: float = 1) -> None:
    if metric is not None and n:
        metric.inc(n)


def _set(metric: Any, v: float) -> None:
    if metric is not None:
        metric.set(v)


@dataclass
class PruneResult:
    total_bytes: int
    pruned_entries: int = 0
    freed_bytes: int = 0
    errors: int = 0
    unprunable_bytes: int = 0


def _dir_size(path: Path) -> int:
    size = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                size += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return size


def scan_cache(cache_dir: Path) -> tuple[list[tuple[float, int, Path]], int, int]:
    """Return ``(entries, total_bytes, unprunable_bytes)``.

    ``entries`` holds ``(mtime, size, path)`` for each filesystem-storage entry
    directory. Any file outside an entry, such as a DBM cache file, counts
    toward ``total_bytes`` and ``unprunable_bytes``.
    """
    entries: list[tuple[float, int, Path]] = []
    total = unprunable = 0
    if not cache_dir.is_dir():
        return entries, 0, 0
    for root, dirs, files in os.walk(cache_dir):
        if ENTRY_MARKER in files:
            entry = Path(root)
            size = _dir_size(entry)
            try:
                mtime = (entry / ENTRY_MARKER).stat().st_mtime
            except OSError:
                mtime = 0.0
            entries.append((mtime, size, entry))
            total += size
            dirs[:] = []  # don't descend into an entry
            continue
        for name in files:
            try:
                sz = os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
            total += sz
            unprunable += sz
    return entries, total, unprunable


def prune_cache(cache_dir: Path, max_bytes: int, target_ratio: float = 0.8) -> PruneResult:
    """Delete the oldest entries until usage is at most ``max_bytes * target_ratio``.

    ``max_bytes <= 0`` only measures. This function never raises.
    """
    try:
        entries, total, unprunable = scan_cache(cache_dir)
    except Exception as e:  # unreadable dir: report, never crash the crawl
        logger.error(f"HTTP cache scan failed for {cache_dir}: {e}")
        _inc(HTTPCACHE_PRUNE_ERRORS)
        return PruneResult(total_bytes=0, errors=1)

    result = PruneResult(total_bytes=total, unprunable_bytes=unprunable)
    if max_bytes > 0 and total > max_bytes:
        target = int(max_bytes * min(max(target_ratio, 0.0), 1.0))
        for _mtime, size, path in sorted(entries, key=lambda e: e[0]):
            if result.total_bytes <= target:
                break
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                pass  # removed concurrently: same outcome
            except OSError as e:
                logger.warning(f"HTTP cache prune could not remove {path}: {e}")
                result.errors += 1
                continue
            result.total_bytes -= size
            result.freed_bytes += size
            result.pruned_entries += 1
        if result.total_bytes > max_bytes:
            logger.warning(
                f"HTTP cache {cache_dir} still {result.total_bytes} bytes after pruning (quota {max_bytes}); "
                f"{unprunable} bytes are in non-prunable files (e.g. a DBM cache). "
                "Use FilesystemCacheStorage for per-entry pruning."
            )
    _inc(HTTPCACHE_PRUNED, result.pruned_entries)
    _inc(HTTPCACHE_PRUNE_ERRORS, result.errors)
    _set(HTTPCACHE_BYTES, result.total_bytes)
    return result


class HttpCacheQuota:
    """Scrapy extension: measure and prune ``HTTPCACHE_DIR`` (#496)."""

    def __init__(self, cache_dir: Path, max_bytes: int, interval: float, target_ratio: float):
        self.cache_dir = cache_dir
        self.max_bytes = max_bytes
        self.interval = interval
        self.target_ratio = target_ratio
        self._task: Any = None
        self.last: PruneResult | None = None

    @classmethod
    def from_crawler(cls, crawler):
        s = crawler.settings
        if not s.getbool("HTTPCACHE_ENABLED"):
            raise NotConfigured("HTTP cache disabled")
        ext = cls(
            cache_dir=Path(data_path(str(s.get("HTTPCACHE_DIR", "httpcache")), createdir=True)),
            max_bytes=max(s.getint("HTTPCACHE_MAX_BYTES", 0), 0),
            interval=s.getfloat("HTTPCACHE_PRUNE_INTERVAL_SECS", 300.0),
            target_ratio=s.getfloat("HTTPCACHE_PRUNE_TARGET_RATIO", 0.8),
        )
        crawler.signals.connect(ext.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(ext.spider_closed, signal=signals.spider_closed)
        return ext

    def prune(self) -> PruneResult:
        started = time.monotonic()
        self.last = prune_cache(self.cache_dir, self.max_bytes, self.target_ratio)
        if self.last.pruned_entries:
            logger.info(
                f"HTTP cache pruned {self.last.pruned_entries} entries ({self.last.freed_bytes} bytes) "
                f"in {time.monotonic() - started:.2f}s; now {self.last.total_bytes}/{self.max_bytes} bytes"
            )
        return self.last

    def spider_opened(self, spider=None):
        _set(HTTPCACHE_QUOTA_BYTES, self.max_bytes)
        self.prune()
        if self.interval > 0:
            from twisted.internet import task

            self._task = task.LoopingCall(self.prune)
            self._task.start(self.interval, now=False)

    def spider_closed(self, spider=None, reason=None):
        if self._task is not None and self._task.running:
            self._task.stop()
        self._task = None
        self.prune()
