"""
SeedManager - Centralized URL seeding and queueing logic.

This module handles:
1. Upserting URLs into seed_urls table (idempotent)
2. Optionally writing to uconn_urls master list
3. Optionally enqueuing URLs to stage2_queue

⚠️  IMPORTANT: Spiders must NEVER write directly to seed/queue tables.
All seeding operations must go through SeedManager to ensure:
- Idempotency (duplicate URLs are handled correctly)
- Consistency (schema matching, URL normalization)
- Centralized logic (no drift between spiders)

Schema Assumptions:
- seed_urls: url (str), url_hash (str), discovered_at (ISO timestamp), source_url (str), source_spider (str)
- uconn_urls: url, url_hash, discovered_at, source_url, source_spider
- stage2_queue: url, url_hash, enqueued_at, status

All writes are idempotent via merge_into using url_hash as merge key.
"""

import logging
import os
import warnings
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlparse

from src.lakehouse.lakehouse_manager import LakehouseManager
from src.utils.url_canon import canonical_or_raw, url_hash
from src.core.timeutil import utc_now_iso

logger = logging.getLogger(__name__)

# Redis depth gate (#1105). Depth is ZCARD of the JS priority queue, the same
# key `redis_queue_length` / QueueBacklog watch. The cutoff is that queue's
# high watermark (10000), not a second limit. Low watermark stays 8000 and
# REDIS_QUEUE_BACKPRESSURE_MODE stays block for admit_enqueue; this gate
# skips the seed write instead of sleeping.
SEED_REDIS_DEPTH_KEY = "js_spider:priority_queue"
SEED_DEPTH_CONFIG_KEY = "seed_enqueue_depth_threshold"

try:
    from prometheus_client import Counter as _SeedCounter

    SEED_ENQUEUE_THROTTLED: Any = _SeedCounter(
        "seed_enqueue_throttled_total",
        "Seed URLs skipped because Redis queue depth was above the high watermark or could not be read.",
    )
except Exception:  # prometheus_client missing or metric already registered
    SEED_ENQUEUE_THROTTLED = None


def _config_get(config: Any, key: str) -> Any:
    if config is None:
        return None
    try:
        return config.get(key)
    except Exception:
        return None


def seed_enqueue_threshold(config: Any = None) -> int:
    """High watermark for the seed gate. Default 10000, same as REDIS_QUEUE_HIGH_WATERMARK.

    ``stage1.seed_enqueue_depth_threshold`` may set that same number. A missing,
    unparsable, or non-positive value uses the watermark. A value that disagrees
    with ``REDIS_QUEUE_HIGH_WATERMARK`` is ignored so the process does not grow
    a second depth limit. The low watermark (8000) and backpressure mode
    (``block``) are not read here.
    """
    from src.utils.redis import queue_watermark_settings

    watermark = int(queue_watermark_settings()["high"])
    if config is None:
        try:
            from src.core.config import get_config

            config = get_config()
        except Exception:
            return watermark
    raw = None
    for prefix in ("stage1", "stages.stage1"):
        raw = _config_get(config, f"{prefix}.{SEED_DEPTH_CONFIG_KEY}")
        if raw is not None:
            break
    if raw is None:
        return watermark
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Ignoring stage1.%s=%r; using Redis high watermark %s",
            SEED_DEPTH_CONFIG_KEY, raw, watermark,
        )
        return watermark
    if value < 1 or value != watermark:
        logger.warning(
            "Ignoring stage1.%s=%r; seed gate uses the Redis high watermark %s",
            SEED_DEPTH_CONFIG_KEY, raw, watermark,
        )
        return watermark
    return value


def _offline_suite() -> bool:
    """pytest.ini sets OBS_OFFLINE=1 so the suite does not dial Redis."""
    return os.getenv("OBS_OFFLINE", "").strip() == "1"


def _connect_depth_client() -> Any | None:
    """Open Redis with REDIS_HOST/PORT/PASSWORD/DB. None when it cannot be read."""
    try:
        import redis
        from src.utils.redis_env import redis_settings

        settings = redis_settings()
        client = redis.Redis(
            host=settings.host,
            port=settings.port,
            db=settings.db,
            password=settings.password or None,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            decode_responses=True,
        )
        client.ping()
        return client
    except Exception as exc:
        logger.warning("[SeedManager] Redis depth unavailable (%s); skipping enqueue", exc)
        return None


def read_seed_queue_depth() -> int | None:
    """ZCARD of the JS priority queue for the seed preflight (#453), or None when unreadable."""
    if _offline_suite():
        return None
    client = _connect_depth_client()
    if client is None:
        return None
    try:
        return int(client.zcard(SEED_REDIS_DEPTH_KEY))
    except Exception as exc:
        logger.warning("[SeedManager] Redis depth read failed (%s)", exc)
        return None


def default_url_hasher(url: str) -> str:
    """SHA256[:16] of the canonical URL (#728), same as every other stage."""
    return url_hash(url)


DEFAULT_DOMAIN_URLS_TABLE = "uconn_urls"  # legacy table name, kept for existing lakes


def _normalize_domains(domains: Iterable[str] | str | None) -> tuple[str, ...]:
    if not domains:
        return ()
    if isinstance(domains, str):
        domains = [domains]
    out = []
    for d in domains:
        d = str(d).strip().lower().lstrip(".")
        if d.startswith("*."):
            d = d[2:]
        if d:
            out.append(d)
    return tuple(dict.fromkeys(out))


def host_in_domains(host: str, domains: Iterable[str]) -> bool:
    """Exact-or-subdomain host match (``www.uconn.edu`` yes, ``uconn.edu.evil.com`` no)."""
    host = (host or "").strip().lower().rstrip(".")
    return bool(host) and any(host == d or host.endswith("." + d) for d in domains)


def domain_urls_policy(config: Any = None) -> dict[str, Any]:
    """Domain side-table policy from config ``stage1.*`` (#317).

    ``write_domain_urls`` defaults to False: a crawl profile opts in (the bundled
    uconn profile in config.yml does), so non-UConn profiles no longer write a
    UConn-branded table.
    """
    if config is None:
        try:
            from src.core.config import get_config

            config = get_config()
        except Exception:
            config = None

    def get(key: str, default: Any = None) -> Any:
        if config is None:
            return default
        for prefix in ("stage1", "stages.stage1"):
            try:
                value = config.get(f"{prefix}.{key}")
            except Exception:
                value = None
            if value is not None:
                return value
        return default

    raw_flag = get("write_domain_urls", False)
    flag = raw_flag.strip().lower() in ("1", "true", "yes", "on") if isinstance(raw_flag, str) else bool(raw_flag)
    return {
        "write_domain_urls": flag,
        "allowed_domains": _normalize_domains(get("allowed_domains", ())),
        "domain_urls_table": str(get("domain_urls_table", DEFAULT_DOMAIN_URLS_TABLE)),
    }


class SeedManager:
    """
    Centralized manager for seed URL expansion and queueing.

    Provides idempotent writes to seed_urls, uconn_urls, and stage2_queue tables.
    This is the ONLY way spiders should add URLs to seeds or queues.

    Example:
        >>> from src.lakehouse import get_lakehouse_manager, SeedManager
        >>> lakehouse = get_lakehouse_manager()
        >>> seed_mgr = SeedManager(lakehouse)
        >>> result = seed_mgr.add_urls_to_seeds(
        ...     urls=["https://example.com/page1"],
        ...     source_url="https://example.com",
        ...     source_spider="scout"
        ... )
        >>> print(result)
        {'seed_inserted': 1, 'uconn_inserted': 1, 'stage2_enqueued': 1}
    """

    def __init__(
        self,
        lakehouse: LakehouseManager | object,
        url_hasher: Callable[[str], str] | None = None,
        *,
        write_domain_urls: bool | None = None,
        allowed_domains: Iterable[str] | None = None,
        domain_urls_table: str | None = None,
        config: Any = None,
        redis_client: Any = None,
    ):
        """
        Initialize SeedManager.

        Args:
            lakehouse: LakehouseManager, or a DeltaHelper-like object with `.manager`
            url_hasher: Optional custom URL hashing function (default: SHA256[:16])
            write_domain_urls: Default for writing the per-domain side table
                (config ``stage1.write_domain_urls``; False when unset, #317).
            allowed_domains: Domains whose URLs go to the side table
                (config ``stage1.allowed_domains``). Hosts match exactly or as
                subdomains, never by substring.
            domain_urls_table: Side-table name (config
                ``stage1.domain_urls_table``; legacy default ``uconn_urls``).
            redis_client: Client used to read queue depth before enqueue.
                When omitted, production connects with REDIS_HOST / REDIS_PORT /
                REDIS_PASSWORD / REDIS_DB. Tests pass a fake client.
        """
        # Scout / get_delta() pass DeltaHelper; historic API took LakehouseManager.
        if hasattr(lakehouse, "merge_into"):
            self.lakehouse = lakehouse
        elif hasattr(lakehouse, "manager"):
            self.lakehouse = lakehouse.manager
        else:
            raise TypeError(
                f"SeedManager expects LakehouseManager or DeltaHelper, got {type(lakehouse)!r}"
            )
        self.url_hasher = url_hasher or default_url_hasher
        policy = domain_urls_policy(config)
        self.write_domain_urls = policy["write_domain_urls"] if write_domain_urls is None else bool(write_domain_urls)
        self.allowed_domains = _normalize_domains(
            policy["allowed_domains"] if allowed_domains is None else allowed_domains
        )
        self.domain_urls_table = domain_urls_table or policy["domain_urls_table"]
        self.redis_client = redis_client
        self._config = config
        self._depth_client: Any = None
        self._depth_probed = False

    def _redis_depth(self) -> int | None:
        """ZCARD of the JS priority queue, or None when depth cannot be read."""
        client = self.redis_client
        if client is None:
            if _offline_suite():
                return None
            if not self._depth_probed:
                self._depth_probed = True
                self._depth_client = _connect_depth_client()
            client = self._depth_client
        if client is None:
            return None
        try:
            return int(client.zcard(SEED_REDIS_DEPTH_KEY))
        except Exception as exc:
            logger.warning("[SeedManager] Redis depth read failed (%s); skipping enqueue", exc)
            return None

    def _depth_blocks_enqueue(self) -> bool:
        # Unit tests that do not inject a client run under OBS_OFFLINE and never
        # read Redis. That is not a depth of 0. A missing or failed client in
        # any other case is unknown depth and must not enqueue.
        if self.redis_client is None and _offline_suite():
            return False
        depth = self._redis_depth()
        if depth is None:
            return True
        # Strictly above the high watermark, matching admit_enqueue (depth > high).
        return depth > seed_enqueue_threshold(self._config)

    def in_allowed_domains(self, url: str) -> bool:
        """True if ``url``'s host is an allowed domain or a subdomain of one."""
        return host_in_domains(urlparse(url).hostname or "", self.allowed_domains)

    def add_urls_to_seeds(
        self,
        urls: Iterable[str],
        source_url: str,
        source_spider: str,
        *,
        write_domain_urls: bool | None = None,
        write_uconn_urls: bool | None = None,
        enqueue_stage2: bool = False,
    ) -> dict[str, int]:
        """
        Add URLs to seed_urls (and optionally the domain side table + stage2_queue).

        This is the primary method for seed URL expansion. All writes are idempotent
        via merge_into operations using url_hash as the merge key.

        Args:
            urls: Iterable of URLs to add
            source_url: Parent URL where these were discovered
            source_spider: Name of spider that discovered these URLs
            write_domain_urls: Also write allowed-domain URLs to the domain side
                table (default: this manager's config-driven ``write_domain_urls``).
            write_uconn_urls: Deprecated alias of ``write_domain_urls``.
            enqueue_stage2: If True, also enqueue to stage2_queue with status=pending (default: False)

        When Redis depth is above the high watermark, or the depth cannot be
        read, no seed, domain, or stage2 row is written and
        ``seed_enqueue_throttled_total`` increments by the number of URLs
        skipped. At or under the watermark, writes proceed. A missing client
        is not treated as depth 0.

        Returns:
            Dictionary with counts:
            {
                "seed_inserted": int,      # URLs merged into seed_urls
                "domain_inserted": int,    # URLs merged into the domain side table
                "uconn_inserted": int,     # legacy alias of domain_inserted
                "stage2_enqueued": int,    # URLs enqueued to stage2_queue
                "rejected": int            # invalid URLs skipped (see url_rejection_reason)
            }

        Examples:
            >>> sm = SeedManager(lakehouse_manager)
            >>> result = sm.add_urls_to_seeds(
            ...     urls=["https://example.com/page1", "https://example.com/page2"],
            ...     source_url="https://example.com",
            ...     source_spider="scout",
            ...     write_domain_urls=True,
            ...     enqueue_stage2=True
            ... )
            >>> print(result)
            {'seed_inserted': 2, 'domain_inserted': 2, 'uconn_inserted': 2, 'stage2_enqueued': 2}
        """
        if write_uconn_urls is not None:
            warnings.warn(
                "write_uconn_urls is deprecated; use write_domain_urls (#317)", DeprecationWarning, stacklevel=2
            )
            if write_domain_urls is None:
                write_domain_urls = write_uconn_urls
        return self.add_seed_pairs(
            ((url, source_url) for url in urls),
            source_spider,
            write_domain_urls=write_domain_urls,
            enqueue_stage2=enqueue_stage2,
        )

    def add_seed_pairs(
        self,
        pairs: Iterable[tuple[str, str]],
        source_spider: str,
        *,
        write_domain_urls: bool | None = None,
        enqueue_stage2: bool = False,
    ) -> dict[str, int]:
        """
        Like ``add_urls_to_seeds`` but each URL carries its own parent.

        ``pairs`` is ``(url, source_url)``. Every row is written by one MERGE per
        table, so a batch of N pages costs one seed MERGE, not N. Dedup and the
        depth gate behave the same as ``add_urls_to_seeds``; the first source
        seen for a url_hash wins.
        """
        write_domain = self.write_domain_urls if write_domain_urls is None else bool(write_domain_urls)

        # Reject non-crawlable seeds (ftp:, javascript:, blanks, host-less, >2 KB)
        # with a stable reason code, canonicalize (#728), then dedupe by
        # url_hash, not just by string: two spellings with one hash would be two
        # source rows for one MERGE key (#255).
        from src.utils.validation import url_rejection_reason

        rejected: dict[str, int] = {}
        by_hash: dict[str, tuple[str, str]] = {}
        for raw, source_url in pairs:
            candidate = raw.strip() if isinstance(raw, str) else raw
            reason = url_rejection_reason(candidate)
            if reason is not None:
                rejected[reason] = rejected.get(reason, 0) + 1
                continue
            canonical = canonical_or_raw(candidate)
            by_hash.setdefault(self.url_hasher(canonical), (canonical, source_url))
        n_rejected = sum(rejected.values())
        if n_rejected:
            logger.warning(f"[SeedManager] Rejected {n_rejected} invalid seed URL(s) from {source_spider}: {rejected}")
        if not by_hash:
            return {"seed_inserted": 0, "domain_inserted": 0, "uconn_inserted": 0, "stage2_enqueued": 0,
                    "rejected": n_rejected}

        # Before enqueue: read Redis depth. Above the high watermark, or unknown, skip.
        if self._depth_blocks_enqueue():
            skipped = len(by_hash)
            if SEED_ENQUEUE_THROTTLED is not None:
                SEED_ENQUEUE_THROTTLED.inc(skipped)
            logger.warning(
                "[SeedManager] Skipping enqueue of %s URL(s) from %s; Redis depth unavailable or above %s",
                skipped,
                source_spider,
                seed_enqueue_threshold(self._config),
            )
            return {
                "seed_inserted": 0,
                "domain_inserted": 0,
                "uconn_inserted": 0,
                "stage2_enqueued": 0,
                "rejected": n_rejected,
            }

        now = utc_now_iso()

        # Prepare base records
        rows = []
        for h, (url, source_url) in by_hash.items():
            rows.append(
                {
                    "url": url,
                    "url_hash": h,
                    "discovered_at": now,
                    "source_url": source_url,
                    "source_spider": source_spider,
                }
            )

        # 1. Upsert into seed_urls
        try:
            self.lakehouse.merge_into(
                "seed_urls",
                rows,
                merge_key="url_hash",
                update_columns=["url", "discovered_at", "source_url", "source_spider"],
            )
            ins_seed = len(rows)
            logger.info(f"[SeedManager] Merged {ins_seed} URLs into seed_urls")
        except Exception as e:
            logger.error(f"[SeedManager] Failed to merge into seed_urls: {e}", exc_info=True)
            ins_seed = 0

        # 2. Optionally write allowed-domain URLs to the domain side table (#317).
        #    Host match is exact-or-subdomain: "uconn.edu.evil.com" and
        #    "notuconn.edu" are not uconn.edu (the old substring test let them in).
        ins_uconn = 0
        if write_domain and not self.allowed_domains:
            logger.warning("[SeedManager] write_domain_urls is on but no allowed_domains are configured; skipping")
        elif write_domain:
            table = self.domain_urls_table
            try:
                domain_rows = [r for r in rows if self.in_allowed_domains(r["url"])]

                if domain_rows:
                    self.lakehouse.merge_into(
                        table,
                        domain_rows,
                        merge_key="url_hash",
                        update_columns=["url", "discovered_at", "source_url", "source_spider"],
                    )
                    ins_uconn = len(domain_rows)
                    logger.info(f"[SeedManager] Merged {ins_uconn} allowed-domain URLs into {table}")
            except Exception as e:
                logger.warning(f"[SeedManager] Failed to merge into {table}: {e}")

        # 3. Optionally enqueue to stage2_queue
        enq = 0
        if enqueue_stage2:
            try:
                qrows = [
                    {
                        "url": r["url"],
                        "url_hash": r["url_hash"],
                        "enqueued_at": now,
                        "status": "pending",
                    }
                    for r in rows
                ]
                self.lakehouse.merge_into(
                    "stage2_queue",
                    qrows,
                    merge_key="url_hash",
                    update_columns=["url", "enqueued_at", "status"],
                )
                enq = len(qrows)
                logger.info(f"[SeedManager] Enqueued {enq} URLs to stage2_queue")
            except Exception as e:
                logger.warning(f"[SeedManager] Failed to enqueue to stage2_queue: {e}")

        return {
            "seed_inserted": ins_seed,
            "domain_inserted": ins_uconn,
            "uconn_inserted": ins_uconn,
            "stage2_enqueued": enq,
            "rejected": n_rejected,
        }

    def bulk_seed_from_list(
        self,
        urls: list[str],
        source_spider: str = "manual",
        batch_size: int = 1000,
    ) -> dict[str, int]:
        """
        Bulk seed URLs from a list (e.g., from sitemap or CSV).

        Args:
            urls: List of URLs to seed
            source_spider: Spider name to attribute (default: "manual")
            batch_size: Batch size for writes (default: 1000)

        Returns:
            Aggregated counts dictionary
        """
        total_results: dict[str, int] = {
            "seed_inserted": 0,
            "domain_inserted": 0,
            "uconn_inserted": 0,
            "stage2_enqueued": 0,
            "rejected": 0,
        }

        for i in range(0, len(urls), batch_size):
            batch = urls[i : i + batch_size]
            result = self.add_urls_to_seeds(
                urls=batch,
                source_url="bulk_import",
                source_spider=source_spider,
                enqueue_stage2=False,  # Bulk imports typically don't enqueue for Stage 2
            )
            for key in total_results:
                total_results[key] += result.get(key, 0)

        logger.info(f"[SeedManager] Bulk seeded {len(urls)} URLs: {total_results}")
        return total_results


# =====================================================================================
# Legacy Compatibility
# =====================================================================================


# For backward compatibility, support the old DeltaLakeManager type
# This will be deprecated in future versions
def create_seed_manager_from_delta(delta_manager) -> SeedManager:
    """
    Create a SeedManager from a DeltaLakeManager (legacy compatibility).

    Args:
        delta_manager: DeltaLakeManager or LakehouseManager instance

    Returns:
        SeedManager instance

    Deprecated:
        Use SeedManager(lakehouse) directly instead.
    """
    logger.warning("create_seed_manager_from_delta() is deprecated. Use SeedManager(lakehouse) directly instead.")
    return SeedManager(delta_manager)
