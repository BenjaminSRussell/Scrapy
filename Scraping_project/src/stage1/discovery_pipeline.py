import json
from datetime import datetime
from pathlib import Path
from itemadapter import ItemAdapter
import logging
import hashlib

logger = logging.getLogger(__name__)


class Stage1Pipeline:
    """Pipeline for Stage 1 Discovery - writes discovered URLs to JSONL"""

    def __init__(self, output_file: str = None):
        self.output_file = Path(output_file or "data/processed/stage01/new_urls.jsonl")
        # persistent hash file to avoid the scale disaster
        self.hash_file = self.output_file.with_suffix('.hashes')

    @classmethod
    def from_crawler(cls, crawler):
        """Create pipeline instance from crawler settings"""
        settings = crawler.settings
        output_file = settings.get('STAGE1_OUTPUT_FILE')
        return cls(output_file)

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

    def process_item(self, item, spider):
        """Process each discovered URL item"""
        adapter = ItemAdapter(item)
        url_hash = adapter.get("url_hash")
        discovered_url = adapter.get("discovered_url")

        if url_hash and discovered_url and url_hash not in self.seen_hashes:
            # Write the discovery data
            discovery_data = {
                "source_url": adapter.get("source_url"),
                "discovered_url": adapter.get("discovered_url"),
                "first_seen": adapter.get("first_seen", datetime.now().isoformat()),
                "url_hash": url_hash,
                "discovery_depth": adapter.get("discovery_depth", 0)
            }

            try:
                self.file.write(json.dumps(discovery_data, ensure_ascii=False) + "\n")
                self.file.flush()  # Ensure data is written immediately
                self.seen_hashes.add(url_hash)
                self.url_count += 1

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
