import json
from datetime import datetime
from pathlib import Path
from itemadapter import ItemAdapter
import logging

logger = logging.getLogger(__name__)


class Stage3Pipeline:
    """Pipeline for Stage 3 Enrichment - writes enriched content to JSONL"""

    def __init__(self, output_file: str = None):
        self.output_file = Path(output_file or "data/processed/stage03/enriched_content.jsonl")

    @classmethod
    def from_crawler(cls, crawler):
        """Create pipeline instance from crawler settings"""
        settings = crawler.settings
        output_file = settings.get('STAGE3_OUTPUT_FILE')
        return cls(output_file)

    def open_spider(self, spider):
        """Initialize pipeline when spider opens"""
        # Ensure output directory exists
        self.output_file.parent.mkdir(parents=True, exist_ok=True)

        self.file = self.output_file.open("a", encoding="utf-8")
        self.item_count = 0

        logger.info(f"[Stage3Pipeline] Writing enriched content to {self.output_file}")

    def close_spider(self, spider):
        """Clean up when spider closes"""
        self.file.close()
        logger.info(f"[Stage3Pipeline] Processed {self.item_count:,} enriched items → {self.output_file}")

    def process_item(self, item, spider):
        """Process each enriched content item"""
        adapter = ItemAdapter(item)

        # Build enrichment data
        enrichment_data = {
            "url": adapter.get("url"),
            "url_hash": adapter.get("url_hash"),
            "title": adapter.get("title", ""),
            "text_content": adapter.get("text_content", ""),
            "word_count": adapter.get("word_count", 0),
            "entities": adapter.get("entities", []),
            "keywords": adapter.get("keywords", []),
            "content_tags": adapter.get("content_tags", []),
            "has_pdf_links": adapter.get("has_pdf_links", False),
            "has_audio_links": adapter.get("has_audio_links", False),
            "processed_at": datetime.now().isoformat()
        }

        try:
            self.file.write(json.dumps(enrichment_data, ensure_ascii=False) + "\n")
            self.file.flush()  # Ensure data is written immediately
            self.item_count += 1

            # Progress logging every 100 items
            if self.item_count % 100 == 0:
                logger.info(f"[Stage3Pipeline] Processed {self.item_count:,} enriched items")

        except Exception as e:
            logger.error(f"[Stage3Pipeline] Error writing enriched item: {e}")

        return item