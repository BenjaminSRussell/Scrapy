import datetime as dt
import hashlib
import logging
from collections.abc import Callable, Iterable
from urllib.parse import urlparse

from src.lakehouse.lakehouse_manager import LakehouseManager

logger = logging.getLogger(__name__)

def default_url_hasher(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]

class SeedManager:

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
                "seed_inserted": int,
                "uconn_inserted": int,
                "stage2_enqueued": int
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
        url_list = list(set(urls))
        if not url_list:
            return {"seed_inserted": 0, "uconn_inserted": 0, "stage2_enqueued": 0}

        now = dt.datetime.utcnow().isoformat()

        rows = []
        for url in url_list:
            rows.append(
                {
                    "url": url,
                    "url_hash": self.url_hasher(url),
                    "discovered_at": now,
                    "source_url": source_url,
                    "source_spider": source_spider,
                }
            )

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

        ins_uconn = 0
        if write_uconn_urls:
            try:
                uconn_rows = [r for r in rows if "uconn.edu" in urlparse(r["url"]).netloc.lower()]

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
                enqueue_stage2=False,
            )
            for key in total_results:
                total_results[key] += result[key]

        logger.info(f"[SeedManager] Bulk seeded {len(urls)} URLs: {total_results}")
        return total_results

# =====================================================================================
# =====================================================================================

def create_seed_manager_from_delta(delta_manager) -> SeedManager:
    logger.warning("create_seed_manager_from_delta() is deprecated. Use SeedManager(lakehouse) directly instead.")
    return SeedManager(delta_manager)
