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

import datetime as dt
import hashlib
import logging
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from src.lakehouse.lakehouse_manager import LakehouseManager

logger = logging.getLogger(__name__)


def default_url_hasher(url: str) -> str:
    """Default URL hasher using SHA256 (first 16 chars)."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


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
        lakehouse: LakehouseManager,
        url_hasher: Callable[[str], str] | None = None,
    ):
        """
        Initialize SeedManager.

        Args:
            lakehouse: LakehouseManager instance for database operations
            url_hasher: Optional custom URL hashing function (default: SHA256[:16])
        """
        self.lakehouse = lakehouse
        self.url_hasher = url_hasher or default_url_hasher

    def add_urls_to_seeds(
        self,
        urls: Iterable[str],
        source_url: str,
        source_spider: str,
        *,
        write_uconn_urls: bool = True,
        enqueue_stage2: bool = False,
    ) -> dict[str, int]:
        """
        Add URLs to seed_urls (and optionally uconn_urls + stage2_queue).

        This is the primary method for seed URL expansion. All writes are idempotent
        via merge_into operations using url_hash as the merge key.

        Args:
            urls: Iterable of URLs to add
            source_url: Parent URL where these were discovered
            source_spider: Name of spider that discovered these URLs
            write_uconn_urls: If True, also write to uconn_urls master list (default: True)
            enqueue_stage2: If True, also enqueue to stage2_queue with status=pending (default: False)

        Returns:
            Dictionary with counts:
            {
                "seed_inserted": int,      # URLs merged into seed_urls
                "uconn_inserted": int,     # URLs merged into uconn_urls
                "stage2_enqueued": int     # URLs enqueued to stage2_queue
            }

        Examples:
            >>> sm = SeedManager(lakehouse_manager)
            >>> result = sm.add_urls_to_seeds(
            ...     urls=["https://example.com/page1", "https://example.com/page2"],
            ...     source_url="https://example.com",
            ...     source_spider="scout",
            ...     write_uconn_urls=True,
            ...     enqueue_stage2=True
            ... )
            >>> print(result)
            {'seed_inserted': 2, 'uconn_inserted': 2, 'stage2_enqueued': 2}
        """
        url_list = list(set(urls))  # Deduplicate
        if not url_list:
            return {"seed_inserted": 0, "uconn_inserted": 0, "stage2_enqueued": 0}

        now = dt.datetime.utcnow().isoformat()

        # Prepare base records
        rows = []
        for url in url_list:
            rows.append({
                "url": url,
                "url_hash": self.url_hasher(url),
                "discovered_at": now,
                "source_url": source_url,
                "source_spider": source_spider,
            })

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

        # 2. Optionally write to uconn_urls (filter to UConn domains only)
        ins_uconn = 0
        if write_uconn_urls:
            try:
                # Filter to only UConn URLs
                uconn_rows = [
                    r for r in rows
                    if "uconn.edu" in urlparse(r["url"]).netloc.lower()
                ]

                if uconn_rows:
                    self.lakehouse.merge_into(
                        "uconn_urls",
                        uconn_rows,
                        merge_key="url_hash",
                        update_columns=["url", "discovered_at", "source_url", "source_spider"],
                    )
                    ins_uconn = len(uconn_rows)
                    logger.info(f"[SeedManager] Merged {ins_uconn} UConn URLs into uconn_urls")
            except Exception as e:
                logger.warning(f"[SeedManager] Failed to merge into uconn_urls: {e}")

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
            "uconn_inserted": ins_uconn,
            "stage2_enqueued": enq,
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
            "uconn_inserted": 0,
            "stage2_enqueued": 0,
        }

        for i in range(0, len(urls), batch_size):
            batch = urls[i : i + batch_size]
            result = self.add_urls_to_seeds(
                urls=batch,
                source_url="bulk_import",
                source_spider=source_spider,
                write_uconn_urls=True,
                enqueue_stage2=False,  # Bulk imports typically don't enqueue for Stage 2
            )
            for key in total_results:
                total_results[key] += result[key]

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
    logger.warning(
        "create_seed_manager_from_delta() is deprecated. "
        "Use SeedManager(lakehouse) directly instead."
    )
    return SeedManager(delta_manager)
