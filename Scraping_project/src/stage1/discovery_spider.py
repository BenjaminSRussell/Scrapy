import csv
from datetime import datetime
from pathlib import Path
from typing import Iterator

import scrapy
from scrapy.linkextractors import LinkExtractor
from scrapy.http import Response

from common.urls import canonicalize_and_hash
from common.schemas import DiscoveryItem


class DiscoverySpider(scrapy.Spider):
    """Stage 1 Discovery Spider - finds and catalogs new URLs"""

    name = "discovery"
    allowed_domains = ["uconn.edu"]

    def __init__(self, max_depth: int = 3, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_depth = int(max_depth)

        # TODO: SCALABILITY CRITICAL - Persistent hash storage needed for large site walks
        # Current in-memory self.url_hashes fails on sites with millions of pages because:
        # - Process restart loses all deduplication history, causing re-crawl of seen pages
        # - Memory usage grows unbounded (millions of 40-byte SHA-1 hashes = GBs of RAM)
        # - Cannot distribute crawl across multiple workers without external coordination
        # Solutions (pick one):
        # 1. Redis SET for distributed deduplication: SADD crawl:seen_hashes {hash}
        # 2. SQLite with hash index: CREATE INDEX idx_hash ON seen_urls(url_hash)
        # 3. Checkpoint rotation: dump hash set every N URLs, load recent checkpoints on startup
        # 4. Bloom filter for probabilistic deduplication (acceptable false positives)
        self.url_hashes = set()

        # Observability counters for real-time crawl coverage measurement
        self.total_urls_parsed = 0
        self.unique_hashes_found = 0
        self.duplicates_skipped = 0
        self.depth_yields = {i: 0 for i in range(self.max_depth + 1)}
        self.referring_pages = {}  # source_url -> count
        self.seed_count = 0

        self.logger.info(f"Discovery spider initialized with max_depth={self.max_depth}")

    def start_requests(self) -> Iterator[scrapy.Request]:
        """Load seed URLs and start crawling"""
        seed_file = Path("data/raw/uconn_urls.csv")

        if not seed_file.exists():
            self.logger.error(f"Seed file not found: {seed_file}")
            return

        self.logger.info(f"Loading seed URLs from {seed_file}")

        with open(seed_file, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row_num, row in enumerate(reader, 1):
                if row and row[0].strip():
                    url = row[0].strip()
                    if url.startswith('http'):
                        try:
                            canonical_url, url_hash = canonicalize_and_hash(url)

                            if url_hash not in self.url_hashes:
                                self.url_hashes.add(url_hash)
                                self.seed_count += 1
                                yield scrapy.Request(
                                    url=canonical_url,
                                    callback=self.parse,
                                    meta={
                                        'source_url': canonical_url,
                                        'depth': 0,
                                        'first_seen': datetime.now().isoformat()
                                    }
                                )
                        except Exception as e:
                            self.logger.error(f"Error processing seed URL at line {row_num}: {url} - {e}")

        # Log seed statistics at startup to detect when seeds go stale
        self.logger.info(f"Loaded {self.seed_count} unique seed URLs (know when seeds go stale)")

    def parse(self, response: Response) -> Iterator[DiscoveryItem]:
        """Parse response and extract links"""
        source_url = response.meta['source_url']
        current_depth = response.meta['depth']
        first_seen = response.meta['first_seen']

        # Extract links from the current page
        le = LinkExtractor(
            allow_domains=self.allowed_domains,
            unique=True,
            deny_extensions=[
                'jpg', 'jpeg', 'png', 'gif', 'svg', 'ico', 'bmp', 'webp',
                'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
                'zip', 'rar', '7z', 'tar', 'gz',
                'mp3', 'mp4', 'avi', 'mov', 'wav', 'flac',
                'css', 'js', 'woff', 'woff2', 'ttf', 'eot',
                'xml', 'rss', 'atom'
            ]
        )

        try:
            links = le.extract_links(response)
            self.total_urls_parsed += 1  # Observability: track total pages parsed

            # Track referring pages for summary
            if source_url not in self.referring_pages:
                self.referring_pages[source_url] = 0
            self.referring_pages[source_url] += len(links)

            # INFO-level progress logging every 100 pages for real-time monitoring
            if self.total_urls_parsed % 100 == 0:
                self.logger.info(f"Crawl Progress: {self.total_urls_parsed} pages parsed, "
                               f"{self.unique_hashes_found} unique URLs, "
                               f"{self.duplicates_skipped} duplicates skipped")

            self.logger.debug(f"Extracted {len(links)} links from {response.url}")

            for link in links:
                try:
                    discovered_url, url_hash = canonicalize_and_hash(link.url)

                    if url_hash not in self.url_hashes:
                        self.url_hashes.add(url_hash)
                        self.unique_hashes_found += 1  # Observability: track unique URLs

                        # Track yields per depth for coverage analysis
                        next_depth = current_depth + 1
                        if next_depth <= self.max_depth:
                            self.depth_yields[next_depth] += 1

                        # Record when this URL was first discovered (preserve lineage)
                        discovery_time = datetime.now().isoformat()

                        # Yield the discovery item
                        yield DiscoveryItem(
                            source_url=source_url,
                            discovered_url=discovered_url,
                            first_seen=discovery_time,
                            url_hash=url_hash,
                            discovery_depth=next_depth
                        )

                        # Continue crawling discovered URLs (respecting depth limit)
                        # CRITICAL: Preserve original discovery timestamp to maintain lineage tracing
                        if current_depth < self.max_depth:
                            yield scrapy.Request(
                                url=discovered_url,
                                callback=self.parse,
                                meta={
                                    'source_url': discovered_url,
                                    'depth': next_depth,
                                    'first_seen': discovery_time  # Preserve original discovery time
                                }
                            )
                    else:
                        self.duplicates_skipped += 1  # Observability: track duplicate URLs

                except Exception as e:
                    self.logger.error(f"Error processing link {link.url}: {e}")

        except Exception as e:
            self.logger.error(f"Error extracting links from {response.url}: {e}")

    def closed(self, reason):
        """Called when spider closes - report comprehensive crawl summary"""
        self.logger.info("=" * 80)
        self.logger.info("DISCOVERY SPIDER CRAWL SUMMARY")
        self.logger.info("=" * 80)

        # Overall statistics
        self.logger.info(f"Crawl completed: {reason}")
        self.logger.info(f"Total pages parsed: {self.total_urls_parsed:,}")
        self.logger.info(f"Unique URLs found: {self.unique_hashes_found:,}")
        self.logger.info(f"Duplicates skipped: {self.duplicates_skipped:,}")
        self.logger.info(f"Seed URLs loaded: {self.seed_count:,}")

        # Depth histogram for coverage analysis
        self.logger.info("-" * 40)
        self.logger.info("DEPTH DISTRIBUTION:")
        total_discovered = sum(self.depth_yields.values())
        for depth in sorted(self.depth_yields.keys()):
            count = self.depth_yields[depth]
            percentage = (count / total_discovered * 100) if total_discovered > 0 else 0
            self.logger.info(f"  Depth {depth}: {count:,} URLs ({percentage:.1f}%)")

        # Top referring pages (helps prune noisy seeds)
        self.logger.info("-" * 40)
        self.logger.info("TOP REFERRING PAGES (for pruning noisy seeds):")
        sorted_referrers = sorted(self.referring_pages.items(), key=lambda x: x[1], reverse=True)
        for i, (source_url, link_count) in enumerate(sorted_referrers[:10], 1):
            self.logger.info(f"  {i}. {link_count:,} links from {source_url}")

        # Efficiency metrics
        duplicate_rate = (self.duplicates_skipped / max(1, self.unique_hashes_found + self.duplicates_skipped)) * 100
        self.logger.info("-" * 40)
        self.logger.info("EFFICIENCY METRICS:")
        self.logger.info(f"Duplicate rate: {duplicate_rate:.1f}% (lower is better)")
        self.logger.info(f"Discovery rate: {self.unique_hashes_found / max(1, self.total_urls_parsed):.1f} URLs/page")

        self.logger.info("=" * 80)