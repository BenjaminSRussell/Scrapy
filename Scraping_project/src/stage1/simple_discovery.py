"""
Stage 1: Fast URL Discovery - Streamlined and Optimized

Goals:
- Maximum speed: Discover URLs as fast as possible
- Minimal processing: Just extract URLs, no heavy analysis
- Efficient deduplication: Bloom filter + hash set
- Write to Delta Lake: ACID guarantees, queryable storage
"""

import asyncio
import hashlib
import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from src.common.constants import DELTA_RAW_URLS
from src.common.delta_lake import DeltaLakeWriter
from src.common.logging import get_logger
from src.common.urls import canonicalize_url_simple

logger = get_logger(__name__)


class BloomFilter:
    """Simple bloom filter for fast URL deduplication."""

    def __init__(self, size: int = 1_000_000):
        self.size = size
        self.bits = [False] * size

    def add(self, item: str):
        """Add item to bloom filter."""
        for i in range(3):  # 3 hash functions
            idx = hash(item + str(i)) % self.size
            self.bits[idx] = True

    def __contains__(self, item: str) -> bool:
        """Check if item might be in set (false positives possible)."""
        return all(
            self.bits[hash(item + str(i)) % self.size]
            for i in range(3)
        )


class FastDiscovery:
    """Fast URL discovery with minimal overhead."""

    def __init__(
        self,
        seed_urls: list[str],
        allowed_domains: list[str],
        max_depth: int = 3,
        max_urls: int = 10000,
        concurrency: int = 64
    ):
        self.seed_urls = seed_urls
        self.allowed_domains = set(allowed_domains)
        self.max_depth = max_depth
        self.max_urls = max_urls
        self.concurrency = concurrency

        # Efficient deduplication
        self.bloom = BloomFilter(size=max_urls * 10)
        self.seen_hashes = set()

        # Queue for BFS crawling
        self.queue = deque()
        for url in seed_urls:
            self.queue.append((url, 0))  # (url, depth)

        # Stats
        self.stats = {
            'discovered': 0,
            'processed': 0,
            'duplicates': 0,
            'errors': 0
        }

        # Batch buffer for Delta Lake writes
        self.batch_buffer = []
        self.batch_size = 100

        # Delta Lake writer
        self.delta_writer = DeltaLakeWriter(
            DELTA_RAW_URLS,
            partition_by=['crawl_date']
        )

    def _url_hash(self, url: str) -> str:
        """Fast URL hash."""
        return hashlib.md5(url.encode()).hexdigest()

    def _is_allowed(self, url: str) -> bool:
        """Check if URL domain is allowed."""
        try:
            domain = urlparse(url).netloc
            return any(
                domain.endswith(allowed) or domain == allowed
                for allowed in self.allowed_domains
            )
        except:
            return False

    def _extract_urls(self, html: str, base_url: str) -> list[str]:
        """Fast URL extraction from HTML."""
        urls = []

        try:
            soup = BeautifulSoup(html, 'lxml')

            # Extract from <a> tags
            for link in soup.find_all('a', href=True):
                href = link['href']
                absolute_url = urljoin(base_url, href)

                # Quick validation
                if absolute_url.startswith(('http://', 'https://')):
                    urls.append(absolute_url)

        except Exception as e:
            logger.debug(f"URL extraction failed: {e}")

        return urls

    async def _fetch_page(
        self,
        session: aiohttp.ClientSession,
        url: str,
        depth: int
    ):
        """Fetch page and extract URLs."""

        try:
            async with session.get(url, timeout=10) as response:
                if response.status != 200:
                    return

                content_type = response.headers.get('Content-Type', '')
                if 'html' not in content_type.lower():
                    return

                html = await response.text()

                # Extract URLs
                urls = self._extract_urls(html, url)

                # Add to queue if within depth limit
                if depth < self.max_depth:
                    for new_url in urls:
                        # Normalize
                        normalized = canonicalize_url_simple(new_url)

                        # Quick bloom filter check
                        if normalized not in self.bloom:
                            url_hash = self._url_hash(normalized)

                            # Precise hash check
                            if url_hash not in self.seen_hashes:
                                if self._is_allowed(normalized):
                                    self.bloom.add(normalized)
                                    self.seen_hashes.add(url_hash)

                                    # Add to queue
                                    self.queue.append((normalized, depth + 1))

                                    # Add to batch buffer
                                    self.batch_buffer.append({
                                        'url': normalized,
                                        'url_hash': url_hash,
                                        'discovered_from': url,
                                        'depth': depth + 1,
                                        'crawl_date': datetime.now().strftime('%Y-%m-%d'),
                                        'discovery_method': 'link_extraction'
                                    })

                                    self.stats['discovered'] += 1

                                    # Flush batch if full
                                    if len(self.batch_buffer) >= self.batch_size:
                                        await self._flush_batch()
                            else:
                                self.stats['duplicates'] += 1

        except Exception as e:
            self.stats['errors'] += 1
            logger.debug(f"Fetch error for {url}: {e}")

        finally:
            self.stats['processed'] += 1

    async def _flush_batch(self):
        """Write batch to Delta Lake."""
        if not self.batch_buffer:
            return

        try:
            self.delta_writer.write(self.batch_buffer, mode='append')
            logger.info(
                f"Wrote {len(self.batch_buffer)} URLs to Delta Lake "
                f"(total: {self.stats['discovered']})"
            )
            self.batch_buffer = []
        except Exception as e:
            logger.error(f"Delta Lake write failed: {e}")

    async def run(self):
        """Run fast discovery."""
        logger.info(f"Starting fast discovery with {len(self.seed_urls)} seeds")
        logger.info(f"Max depth: {self.max_depth}, Max URLs: {self.max_urls}")

        start_time = datetime.now()

        # Create aiohttp session
        connector = aiohttp.TCPConnector(limit=self.concurrency)
        timeout = aiohttp.ClientTimeout(total=10)

        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout
        ) as session:

            # Process queue
            active_tasks = set()

            while self.queue or active_tasks:
                # Fill up to concurrency limit
                while len(active_tasks) < self.concurrency and self.queue:
                    if self.stats['discovered'] >= self.max_urls:
                        break

                    url, depth = self.queue.popleft()
                    task = asyncio.create_task(
                        self._fetch_page(session, url, depth)
                    )
                    active_tasks.add(task)

                # Wait for any task to complete
                if active_tasks:
                    done, active_tasks = await asyncio.wait(
                        active_tasks,
                        return_when=asyncio.FIRST_COMPLETED
                    )

                # Stop if hit limit
                if self.stats['discovered'] >= self.max_urls:
                    # Cancel remaining tasks
                    for task in active_tasks:
                        task.cancel()
                    break

            # Flush remaining batch
            await self._flush_batch()

        elapsed = (datetime.now() - start_time).total_seconds()

        logger.info("=" * 60)
        logger.info("STAGE 1 COMPLETE")
        logger.info(f"Discovered: {self.stats['discovered']} URLs")
        logger.info(f"Processed: {self.stats['processed']} pages")
        logger.info(f"Duplicates filtered: {self.stats['duplicates']}")
        logger.info(f"Errors: {self.stats['errors']}")
        logger.info(f"Time: {elapsed:.1f}s")
        logger.info(f"Rate: {self.stats['discovered'] / elapsed:.1f} URLs/sec")
        logger.info("=" * 60)


async def run_discovery(
    seed_urls: list[str],
    allowed_domains: list[str],
    max_depth: int = 3,
    max_urls: int = 10000,
    concurrency: int = 64
):
    """Main entry point for fast discovery."""
    discovery = FastDiscovery(
        seed_urls=seed_urls,
        allowed_domains=allowed_domains,
        max_depth=max_depth,
        max_urls=max_urls,
        concurrency=concurrency
    )

    await discovery.run()


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Fast URL Discovery')
    parser.add_argument('--seeds', nargs='+', required=True, help='Seed URLs')
    parser.add_argument('--domains', nargs='+', required=True, help='Allowed domains')
    parser.add_argument('--max-depth', type=int, default=3, help='Max crawl depth')
    parser.add_argument('--max-urls', type=int, default=10000, help='Max URLs to discover')
    parser.add_argument('--concurrency', type=int, default=64, help='Concurrent requests')

    args = parser.parse_args()

    asyncio.run(run_discovery(
        seed_urls=args.seeds,
        allowed_domains=args.domains,
        max_depth=args.max_depth,
        max_urls=args.max_urls,
        concurrency=args.concurrency
    ))


if __name__ == '__main__':
    main()
