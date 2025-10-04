import json
import logging
from datetime import datetime
from pathlib import Path

from itemadapter import ItemAdapter

logger = logging.getLogger(__name__)


class Stage1Pipeline:
    """Pipeline for Stage 1 Discovery - writes discovered URLs to JSONL with buffered I/O"""
    def __init__(self, output_file: str = None):
        self.output_file = Path(output_file or "data/processed/stage01/discovery_output.jsonl")
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
        self.file = self.output_file.open("a", encoding="utf-8", buffering=8192)
        self.seen_hashes = set()
        self.url_count = 0
        self.write_buffer = []
        self.buffer_size = 100

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
                # Rebuild hash file from JSONL if corrupted
                self._migrate_from_jsonl()
        else:
            # First run or missing hash file - build from existing JSONL
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
        if self.write_buffer:
            self.file.writelines(self.write_buffer)
            self.write_buffer.clear()
        self.file.close()
        self._save_hashes()
        logger.info(f"[Stage1Pipeline] Discovered {self.url_count:,} new URLs → {self.output_file}")
        logger.info(f"[Stage1Pipeline] Hash index updated: {len(self.seen_hashes):,} total hashes")
        logger.info("[Stage1Pipeline] Run 'python tools/analyze_link_graph.py' to analyze link graph")

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
                self.write_buffer.append(json.dumps(discovery_data, ensure_ascii=False) + "\n")
                self.seen_hashes.add(url_hash)
                self.url_count += 1

                if len(self.write_buffer) >= self.buffer_size:
                    self.file.writelines(self.write_buffer)
                    self.write_buffer.clear()

            except Exception as e:
                logger.error(f"[Stage1Pipeline] Error writing item: {e}")

        return item
