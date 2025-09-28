import json
from datetime import datetime
from pathlib import Path
from itemadapter import ItemAdapter
import logging

logger = logging.getLogger(__name__)


class Stage1Pipeline:
    """Pipeline for Stage 1 Discovery - writes discovered URLs to JSONL"""

    def __init__(self, output_file: str = None):
        self.output_file = Path(output_file or "data/processed/stage01/new_urls.jsonl")

    @classmethod
    def from_crawler(cls, crawler):
        """Create pipeline instance from crawler settings"""
        settings = crawler.settings
        output_file = settings.get('STAGE1_OUTPUT_FILE')
        return cls(output_file)

    def open_spider(self, spider):
        """Initialize pipeline when spider opens"""
        # make sure the folder exists or things break
        self.output_file.parent.mkdir(parents=True, exist_ok=True)

        self.file = self.output_file.open("a", encoding="utf-8")
        self.seen_hashes = set()
        self.url_count = 0

        # load existing hashes with some attempt at efficiency
        # still loads everything because fixing this properly would require actual effort
        if self.output_file.exists():
            # at least limit memory usage by reading only the last N lines
            max_lines_to_read = 100000  # arbitrary limit to prevent total meltdown
            lines_read = 0

            with self.output_file.open("r", encoding="utf-8") as f:
                # seek to end and work backwards if file is huge
                f.seek(0, 2)  # go to end
                file_size = f.tell()

                if file_size > 50 * 1024 * 1024:  # 50MB threshold
                    # skip to last 10MB for huge files - sorry for the hack
                    f.seek(max(0, file_size - 10 * 1024 * 1024))
                    f.readline()  # skip partial line
                else:
                    f.seek(0)

                for line in f:
                    if lines_read >= max_lines_to_read:
                        break
                    try:
                        data = json.loads(line)
                        url_hash = data.get("url_hash")
                        if url_hash:
                            self.seen_hashes.add(url_hash)
                        lines_read += 1
                    except json.JSONDecodeError:
                        continue

        logger.info(f"[Stage1Pipeline] Loaded {len(self.seen_hashes):,} existing URL hashes")
        logger.info(f"[Stage1Pipeline] Writing to {self.output_file}")

    def close_spider(self, spider):
        """Clean up when spider closes"""
        self.file.close()
        logger.info(f"[Stage1Pipeline] Discovered {self.url_count:,} new URLs → {self.output_file}")

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
