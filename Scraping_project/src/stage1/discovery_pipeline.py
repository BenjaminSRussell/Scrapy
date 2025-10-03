# TODO: Add support for more flexible storage of the seen hashes, such as using a database or a Bloom filter.
import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from itemadapter import ItemAdapter

from src.common.link_graph import LinkGraphAnalyzer

logger = logging.getLogger(__name__)


class Stage1Pipeline:
    """Pipeline for Stage 1 Discovery - writes discovered URLs to JSONL and builds link graph"""

    # TODO: The output file is hardcoded. It should be configurable.
    def __init__(self, output_file: str = None, enable_link_graph: bool = True):
        self.output_file = Path(output_file or "data/processed/stage01/discovery_output.jsonl")
        # persistent hash file to avoid the scale disaster
        self.hash_file = self.output_file.with_suffix('.hashes')

        # Link graph integration
        self.enable_link_graph = enable_link_graph
        self.link_graph: LinkGraphAnalyzer = None
        self._page_outlinks: dict[str, set[str]] = defaultdict(set)
        self._page_depths: dict[str, int] = {}

    @classmethod
    def from_crawler(cls, crawler):
        """Create pipeline instance from crawler settings"""
        settings = crawler.settings
        output_file = settings.get('STAGE1_OUTPUT_FILE')
        enable_link_graph = settings.getbool('ENABLE_LINK_GRAPH', True)
        return cls(output_file, enable_link_graph)

    def open_spider(self, spider):
        """Initialize pipeline when spider opens"""
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.output_file.open("a", encoding="utf-8")
        self.seen_hashes = set()
        self.url_count = 0

        # load ALL existing hashes from persistent file - no more arbitrary limits
        self._load_all_hashes()

        logger.info(f"[Stage1Pipeline] Loaded {len(self.seen_hashes):,} existing URL hashes")
        logger.info(f"[Stage1Pipeline] Writing to {self.output_file}")

        # Initialize link graph analyzer
        if self.enable_link_graph:
            link_graph_db = Path("data/processed/link_graph.db")
            self.link_graph = LinkGraphAnalyzer(link_graph_db)
            logger.info(f"[Stage1Pipeline] Link graph analysis enabled: {link_graph_db}")

    def _load_all_hashes(self):
        """Load all seen hashes from persistent storage - properly this time"""
        if self.hash_file.exists():
            try:
                with self.hash_file.open("r", encoding="utf-8") as f:
                    for line in f:
                        hash_val = line.strip()
                        if hash_val:
                            self.seen_hashes.add(hash_val)
            except Exception as e:
                logger.warning(f"[Stage1Pipeline] Error loading hashes: {e}")
                # fallback to old method if hash file is corrupted
                self._migrate_from_jsonl()
        else:
            # first run or missing hash file - build from existing JSONL
            self._migrate_from_jsonl()

    def _migrate_from_jsonl(self):
        """One-time migration from JSONL to hash file - all hashes, no limits"""
        if not self.output_file.exists():
            return

        logger.info("[Stage1Pipeline] Building hash index from existing JSONL...")
        with self.output_file.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                try:
                    data = json.loads(line)
                    url_hash = data.get("url_hash")
                    if url_hash:
                        self.seen_hashes.add(url_hash)

                    if line_num % 50000 == 0:
                        logger.info(f"[Stage1Pipeline] Processed {line_num:,} lines, {len(self.seen_hashes):,} hashes")

                except json.JSONDecodeError:
                    continue

        # write all hashes to persistent file
        self._save_hashes()
        logger.info(f"[Stage1Pipeline] Migration complete: {len(self.seen_hashes):,} hashes indexed")

    def _save_hashes(self):
        """Save all current hashes to persistent file"""
        with self.hash_file.open("w", encoding="utf-8") as f:
            for hash_val in self.seen_hashes:
                f.write(f"{hash_val}\n")

    def close_spider(self, spider):
        """Clean up when spider closes"""
        self.file.close()
        # save updated hash set for next run
        self._save_hashes()
        logger.info(f"[Stage1Pipeline] Discovered {self.url_count:,} new URLs → {self.output_file}")
        logger.info(f"[Stage1Pipeline] Hash index updated: {len(self.seen_hashes):,} total hashes")

        # Build link graph and calculate importance scores
        if self.enable_link_graph and self.link_graph:
            logger.info("[Stage1Pipeline] Building link graph from discovered URLs...")

            # Add all pages with their outlinks to the graph
            for source_url, outlinks in self._page_outlinks.items():
                depth = self._page_depths.get(source_url, 0)
                self.link_graph.add_page(source_url, list(outlinks), depth=depth)

            logger.info(f"[Stage1Pipeline] Added {len(self._page_outlinks):,} pages to link graph")

            # Calculate PageRank scores
            logger.info("[Stage1Pipeline] Calculating PageRank scores...")
            pagerank_scores = self.link_graph.calculate_pagerank()
            logger.info(f"[Stage1Pipeline] Calculated PageRank for {len(pagerank_scores):,} URLs")

            # Calculate HITS algorithm scores
            logger.info("[Stage1Pipeline] Calculating HITS (hub/authority) scores...")
            hub_scores, authority_scores = self.link_graph.calculate_hits()
            logger.info(f"[Stage1Pipeline] Calculated HITS scores for {len(hub_scores):,} URLs")

            # Print graph statistics
            stats = self.link_graph.get_graph_stats()
            logger.info("=" * 60)
            logger.info("LINK GRAPH STATISTICS:")
            logger.info(f"  Total nodes: {stats.total_nodes:,}")
            logger.info(f"  Total edges: {stats.total_edges:,}")
            logger.info(f"  Average degree: {stats.avg_degree}")
            logger.info(f"  Max degree: {stats.max_degree}")

            if stats.top_pages_by_pagerank:
                logger.info("\nTop 5 pages by PageRank:")
                for i, (url, score) in enumerate(stats.top_pages_by_pagerank[:5], 1):
                    logger.info(f"  {i}. {score:.4f} - {url}")

            if stats.top_authorities:
                logger.info("\nTop 5 authorities (HITS):")
                for i, (url, score) in enumerate(stats.top_authorities[:5], 1):
                    logger.info(f"  {i}. {score:.4f} - {url}")

            logger.info("=" * 60)

    # TODO: This item processing is very basic. It should be extended to support more complex scenarios, such as handling different item types or enriching items with additional metadata.
    def process_item(self, item, spider):
        """Process each discovered URL item"""
        adapter = ItemAdapter(item)
        url_hash = adapter.get("url_hash")
        discovered_url = adapter.get("discovered_url")
        source_url = adapter.get("source_url")
        discovery_depth = adapter.get("discovery_depth", 0)

        if url_hash and discovered_url and url_hash not in self.seen_hashes:
            # Write the discovery data with provenance flags for troubleshooting
            discovery_data = {
                "source_url": source_url,
                "discovered_url": discovered_url,
                "first_seen": adapter.get("first_seen", datetime.now().isoformat()),
                "url_hash": url_hash,
                "discovery_depth": discovery_depth,
                "discovery_source": adapter.get("discovery_source", "unknown"),
                "confidence": adapter.get("confidence", 0.0),
                "importance_score": adapter.get("importance_score", 0.0),
                "anchor_text": adapter.get("anchor_text"),
                "is_same_domain": adapter.get("is_same_domain", True)
            }

            try:
                self.file.write(json.dumps(discovery_data, ensure_ascii=False) + "\n")
                self.file.flush()  # Ensure data is written immediately
                self.seen_hashes.add(url_hash)
                self.url_count += 1

                # Build link graph: track source -> discovered_url relationship
                if self.enable_link_graph and source_url and discovered_url:
                    self._page_outlinks[source_url].add(discovered_url)
                    # Track depth for graph metadata
                    if source_url not in self._page_depths:
                        self._page_depths[source_url] = max(0, discovery_depth - 1)
                    if discovered_url not in self._page_depths:
                        self._page_depths[discovered_url] = discovery_depth

                # more spam logs every 1000 because why not
                if self.url_count % 1000 == 0:
                    total_items_processed = len(self.seen_hashes)
                    duplicate_rate = ((total_items_processed - self.url_count) / max(1, total_items_processed)) * 100
                    logger.info(f"[Stage1Pipeline] Processed {self.url_count:,} unique URLs, "
                              f"{total_items_processed:,} total items, "
                              f"{duplicate_rate:.1f}% duplicates skipped")

            except Exception as e:
                logger.error(f"[Stage1Pipeline] Error writing item: {e}")

        return item
