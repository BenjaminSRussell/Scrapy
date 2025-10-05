import json
import logging
from datetime import datetime
from pathlib import Path

from itemadapter import ItemAdapter

# Use a try-except block for optional dependency
try:
    from src.common.delta_lake import write_raw_urls, read_raw_urls, DELTA_AVAILABLE
except ImportError:
    DELTA_AVAILABLE = False
    write_raw_urls = None
    read_raw_urls = None

logger = logging.getLogger(__name__)


class Stage1Pipeline:
    """Pipeline for Stage 1 Discovery - writes discovered URLs to JSONL or Delta Lake"""

    def __init__(self, output_file: str = None, storage_config: dict = None):
        self.output_file = Path(output_file or "data/processed/stage01/discovery_output.jsonl")
        self.storage_config = storage_config or {}
        self.storage_backend = self.storage_config.get("backend", "jsonl")
        self.hash_file = self.output_file.with_suffix('.hashes')
        self.file = None
        self.write_buffer = []
        self.buffer_size = 100
        self.seen_hashes = set()
        self.url_count = 0

    @classmethod
    def from_crawler(cls, crawler):
        """Create pipeline instance from crawler settings"""
        settings = crawler.settings
        output_file = settings.get('STAGE1_OUTPUT_FILE')
        # Access the unified config object from the spider
        stage1_config = getattr(crawler.spider, 'config', {}).get('stages', {}).get('discovery', {})
        storage_config = stage1_config.get('storage', {})
        return cls(output_file, storage_config)

    def open_spider(self, spider):
        """Initialize pipeline when spider opens"""
        if self.storage_backend == 'delta':
            if not DELTA_AVAILABLE:
                raise RuntimeError("Delta Lake backend is configured, but 'deltalake' is not installed.")
            logger.info("[Stage1Pipeline] Using Delta Lake backend.")
            self._load_hashes_from_delta()
        else:  # jsonl backend
            self.output_file.parent.mkdir(parents=True, exist_ok=True)
            self.file = self.output_file.open("a", encoding="utf-8", buffering=8192)
            self._load_all_hashes_from_file()
            logger.info(f"[Stage1Pipeline] Writing to {self.output_file}")

        logger.info(f"[Stage1Pipeline] Loaded {len(self.seen_hashes):,} existing URL hashes")

    def _load_hashes_from_delta(self):
        """Load all seen hashes from the raw_urls Delta table."""
        logger.info("[Stage1Pipeline] Loading existing hashes from Delta Lake...")
        try:
            existing_records = read_raw_urls(columns=['url_hash'])
            self.seen_hashes = {record['url_hash'] for record in existing_records if 'url_hash' in record}
        except FileNotFoundError:
            logger.info("[Stage1Pipeline] raw_urls Delta table not found. Starting fresh.")
            self.seen_hashes = set()
        except Exception as e:
            logger.error(f"[Stage1Pipeline] Error loading hashes from Delta Lake: {e}", exc_info=True)
            self.seen_hashes = set()

    def _load_all_hashes_from_file(self):
        """Load all seen hashes from persistent storage (for jsonl backend)"""
        if self.hash_file.exists():
            try:
                with self.hash_file.open("r", encoding="utf-8") as f:
                    self.seen_hashes.update(line.strip() for line in f if line.strip())
            except Exception as e:
                logger.warning(f"[Stage1Pipeline] Error loading hashes from file: {e}")
                self._migrate_from_jsonl()
        else:
            self._migrate_from_jsonl()

    def _migrate_from_jsonl(self):
        """One-time migration from JSONL to hash file."""
        if not self.output_file.exists():
            return
        logger.info("[Stage1Pipeline] Building hash index from existing JSONL...")
        with self.output_file.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                try:
                    url_hash = json.loads(line).get("url_hash")
                    if url_hash:
                        self.seen_hashes.add(url_hash)
                    if line_num % 50000 == 0:
                        logger.info(f"  ...processed {line_num:,} lines")
                except json.JSONDecodeError:
                    continue
        self._save_hashes_to_file()
        logger.info(f"[Stage1Pipeline] Hash index migration complete: {len(self.seen_hashes):,} hashes.")

    def _save_hashes_to_file(self):
        """Save all current hashes to a persistent file."""
        with self.hash_file.open("w", encoding="utf-8") as f:
            for hash_val in self.seen_hashes:
                f.write(f"{hash_val}\n")

    def close_spider(self, spider):
        """Clean up when spider closes"""
        self._flush_buffer()
        if self.file:
            self.file.close()

        if self.storage_backend == 'jsonl':
            self._save_hashes_to_file()
            logger.info(f"Discovered {self.url_count:,} new URLs -> {self.output_file}")
        else:
            logger.info(f"Discovered and wrote {self.url_count:,} new URLs to Delta Lake.")

    def _flush_buffer(self):
        """Flush the write buffer to the configured storage."""
        if not self.write_buffer:
            return

        try:
            if self.storage_backend == 'delta':
                write_raw_urls(self.write_buffer)
            else:  # jsonl
                self.file.writelines(self.write_buffer)
        except Exception as e:
            logger.error(f"[Stage1Pipeline] Failed to write buffer: {e}", exc_info=True)
        finally:
            self.write_buffer.clear()

    def process_item(self, item, spider):
        """Process each discovered URL item"""
        adapter = ItemAdapter(item)
        url_hash = adapter.get("url_hash")

        if url_hash and url_hash not in self.seen_hashes:
            self.seen_hashes.add(url_hash)
            self.url_count += 1

            # Prepare data for storage
            discovery_data = {
                "source_url": adapter.get("source_url"),
                "discovered_url": adapter.get("discovered_url"),
                "first_seen": adapter.get("first_seen", datetime.now().isoformat()),
                "url_hash": url_hash,
                "discovery_depth": adapter.get("discovery_depth", 0),
                "discovery_source": adapter.get("discovery_source", "unknown"),
                "confidence": adapter.get("confidence", 0.0),
                "importance_score": adapter.get("importance_score", 0.0),
                "anchor_text": adapter.get("anchor_text"),
                "is_same_domain": adapter.get("is_same_domain", True)
            }

            if self.storage_backend == 'delta':
                self.write_buffer.append(discovery_data)
            else:
                self.write_buffer.append(json.dumps(discovery_data, ensure_ascii=False) + "\n")

            if len(self.write_buffer) >= self.buffer_size:
                self._flush_buffer()

        return item