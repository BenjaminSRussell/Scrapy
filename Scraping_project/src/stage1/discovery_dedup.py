"""Shared dual-discovery dedup for Python SitemapParser/Scout and rustmapper.

Default mode is shared-dedup: atomic claim keys ``seen:{site}:{url_hash}``
before enqueue so both engines yield ≤1 pending Stage2 row per URL per job.

Optional mutex mode serializes whole-site discovery under
``discovery_mutex:{job_id}:{site}``.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class DiscoveryDedupMode(str, Enum):
    """Configured dual-discovery coordination modes."""

    SHARED_DEDUP = "shared_dedup"
    MUTEX = "mutex"


def url_hash(url: str) -> str:
    """Stable short hash used in Redis claim keys (matches SeedManager default)."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def site_key(url_or_host: str) -> str:
    """Normalize a URL or bare host into a site identifier."""
    parsed = urlparse(url_or_host if "://" in url_or_host else f"https://{url_or_host}")
    host = (parsed.netloc or parsed.path or url_or_host).lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


def seen_claim_key(site: str, hashed: str, job_id: str | None = None) -> str:
    """Build Redis key ``seen:{site}:{url_hash}`` (optionally job-scoped)."""
    site_norm = site_key(site)
    if job_id:
        return f"seen:{job_id}:{site_norm}:{hashed}"
    return f"seen:{site_norm}:{hashed}"


def mutex_key(site: str, job_id: str) -> str:
    """Build per-job discovery mutex key."""
    return f"discovery_mutex:{job_id}:{site_key(site)}"


@dataclass
class ClaimResult:
    """Outcome of an atomic discovery claim."""

    url: str
    url_hash: str
    claimed: bool
    discovery_source: str
    prior_source: str | None = None


class DiscoveryDedup:
    """Coordinate dual discovery engines via shared-dedup or mutex.

    Shared-dedup (default): ``SET seen:{site}:{url_hash} NX`` stores
    ``discovery_source`` provenance; only the first claimer enqueues.

    Mutex: acquire ``discovery_mutex:{job_id}:{site}`` for exclusive
    discovery of a host within a job; still records per-URL claims for
    Stage2 uniqueness.
    """

    DEFAULT_TTL_SECONDS = 7 * 24 * 3600  # 7 days

    def __init__(
        self,
        redis_client: Any | None = None,
        *,
        mode: DiscoveryDedupMode | str = DiscoveryDedupMode.SHARED_DEDUP,
        job_id: str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ):
        self.redis = redis_client
        self.mode = DiscoveryDedupMode(mode) if not isinstance(mode, DiscoveryDedupMode) else mode
        self.job_id = job_id or "default"
        self.ttl_seconds = ttl_seconds
        self._local_seen: dict[str, str] = {}
        self._local_lock = threading.Lock()
        self._mutex_holders: dict[str, str] = {}

    def claim_url(
        self,
        url: str,
        discovery_source: str,
        *,
        site: str | None = None,
    ) -> ClaimResult:
        """Atomically claim a URL for enqueue. Returns claimed=False if already seen."""
        hashed = url_hash(url)
        site_id = site_key(site or url)
        key = seen_claim_key(site_id, hashed, self.job_id if self.mode == DiscoveryDedupMode.SHARED_DEDUP else None)

        if self.redis is not None:
            try:
                # SET NX EX — atomic claim with provenance
                ok = self.redis.set(key, discovery_source, nx=True, ex=self.ttl_seconds)
                if ok:
                    return ClaimResult(
                        url=url,
                        url_hash=hashed,
                        claimed=True,
                        discovery_source=discovery_source,
                    )
                prior = self.redis.get(key)
                if isinstance(prior, bytes):
                    prior = prior.decode("utf-8", errors="ignore")
                return ClaimResult(
                    url=url,
                    url_hash=hashed,
                    claimed=False,
                    discovery_source=discovery_source,
                    prior_source=prior,
                )
            except Exception as exc:
                logger.warning("Redis claim failed for %s (%s); falling back to local", url, exc)

        with self._local_lock:
            if key in self._local_seen:
                return ClaimResult(
                    url=url,
                    url_hash=hashed,
                    claimed=False,
                    discovery_source=discovery_source,
                    prior_source=self._local_seen[key],
                )
            self._local_seen[key] = discovery_source
            return ClaimResult(
                url=url,
                url_hash=hashed,
                claimed=True,
                discovery_source=discovery_source,
            )

    def claim_urls(
        self,
        urls: Iterable[str],
        discovery_source: str,
        *,
        site: str | None = None,
    ) -> tuple[list[str], list[ClaimResult]]:
        """Claim many URLs; return (newly_claimed_urls, all_results)."""
        claimed: list[str] = []
        results: list[ClaimResult] = []
        for url in urls:
            result = self.claim_url(url, discovery_source, site=site)
            results.append(result)
            if result.claimed:
                claimed.append(url)
        return claimed, results

    def acquire_mutex(self, site: str, discovery_source: str) -> bool:
        """Acquire per-job site mutex (mutex mode). Always True in shared_dedup mode."""
        if self.mode != DiscoveryDedupMode.MUTEX:
            return True

        key = mutex_key(site, self.job_id)
        if self.redis is not None:
            try:
                ok = self.redis.set(key, discovery_source, nx=True, ex=self.ttl_seconds)
                return bool(ok)
            except Exception as exc:
                logger.warning("Redis mutex acquire failed for %s: %s", site, exc)

        with self._local_lock:
            if key in self._mutex_holders and self._mutex_holders[key] != discovery_source:
                return False
            self._mutex_holders[key] = discovery_source
            return True

    def release_mutex(self, site: str, discovery_source: str) -> None:
        """Release per-job site mutex if held by ``discovery_source``."""
        if self.mode != DiscoveryDedupMode.MUTEX:
            return

        key = mutex_key(site, self.job_id)
        if self.redis is not None:
            try:
                current = self.redis.get(key)
                if isinstance(current, bytes):
                    current = current.decode("utf-8", errors="ignore")
                if current == discovery_source:
                    self.redis.delete(key)
            except Exception as exc:
                logger.warning("Redis mutex release failed for %s: %s", site, exc)
            return

        with self._local_lock:
            if self._mutex_holders.get(key) == discovery_source:
                del self._mutex_holders[key]


def get_discovery_dedup_from_config(
    config: Any | None = None,
    redis_client: Any | None = None,
    job_id: str | None = None,
) -> DiscoveryDedup:
    """Build DiscoveryDedup from pipeline config (stage1.discovery.*)."""
    mode = DiscoveryDedupMode.SHARED_DEDUP
    ttl = DiscoveryDedup.DEFAULT_TTL_SECONDS
    cfg_job = job_id

    if config is not None:
        getter = config.get if hasattr(config, "get") else (lambda k, d=None: d)
        # Support both stage1.* and stages.stage1.* layouts
        disc = getter("stage1.discovery") or getter("stages.stage1.discovery") or {}
        if isinstance(disc, dict):
            raw_mode = disc.get("mode", "shared_dedup")
            mode = DiscoveryDedupMode(raw_mode)
            ttl = int(disc.get("claim_ttl_seconds", ttl))
            cfg_job = cfg_job or disc.get("job_id")

    return DiscoveryDedup(
        redis_client=redis_client,
        mode=mode,
        job_id=cfg_job,
        ttl_seconds=ttl,
    )
