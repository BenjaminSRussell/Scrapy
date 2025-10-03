"""
Data Warehouse Pipeline

Scrapy pipeline that writes enriched content to the data warehouse (SQLite or PostgreSQL).
Handles versioning, change tracking, and relational normalization.
"""

import hashlib
import logging
from datetime import datetime

from scrapy import Spider
from scrapy.exceptions import NotConfigured

from src.common.warehouse import DataWarehouse
from src.common.warehouse_schema import (
    CategoryRecord,
    DatabaseType,
    EntityRecord,
    KeywordRecord,
    PageRecord,
)

logger = logging.getLogger(__name__)


class DataWarehousePipeline:
    """Pipeline to write enriched items to data warehouse"""

    def __init__(self, db_type: str = "sqlite", connection_string: str | None = None, crawl_version: int = 1):
        self.db_type = DatabaseType.SQLITE if db_type == "sqlite" else DatabaseType.POSTGRESQL
        self.connection_string = connection_string
        self.warehouse = None
        self.crawl_version = crawl_version
        self.items_processed = 0

    @classmethod
    def from_crawler(cls, crawler):
        """Initialize from Scrapy crawler settings"""
        settings = crawler.settings

        # Get database configuration
        db_type = settings.get('WAREHOUSE_DB_TYPE', 'sqlite')
        connection_string = settings.get('WAREHOUSE_CONNECTION_STRING')

        # Get current crawl version (incremented each run)
        crawl_version = settings.get('WAREHOUSE_CRAWL_VERSION', 1)

        if not settings.getbool('WAREHOUSE_ENABLED', True):
            raise NotConfigured('Data warehouse pipeline is disabled')

        return cls(db_type=db_type, connection_string=connection_string, crawl_version=crawl_version)

    def open_spider(self, spider: Spider):
        """Initialize warehouse connection when spider opens"""
        self.warehouse = DataWarehouse(
            db_type=self.db_type,
            connection_string=self.connection_string,
            auto_create_schema=True
        )
        logger.info(f"Data warehouse pipeline opened: {self.db_type.value}")

    def close_spider(self, spider: Spider):
        """Log statistics when spider closes"""
        logger.info(f"Data warehouse pipeline closed. Items processed: {self.items_processed}")

    def process_item(self, item, spider: Spider):
        """Process enriched item and write to warehouse"""
        try:
            # Create page record
            page = PageRecord(
                url=item.get('url', ''),
                url_hash=item.get('url_hash', self._generate_hash(item.get('url', ''))),
                title=item.get('title'),
                text_content=item.get('text_content'),
                word_count=item.get('word_count', 0),
                content_type=item.get('content_type'),
                status_code=item.get('status_code'),
                has_pdf_links=item.get('has_pdf_links', False),
                has_audio_links=item.get('has_audio_links', False),
                crawl_version=self.crawl_version,
                last_crawled_at=datetime.now()
            )

            # Insert page and get page_id
            page_id = self.warehouse.insert_page(page)

            # Insert entities
            entities = []
            for entity_text in item.get('entities', []):
                entities.append(EntityRecord(
                    page_id=page_id,
                    entity_text=entity_text,
                    source='nlp',
                    crawl_version=self.crawl_version
                ))

            if entities:
                self.warehouse.insert_entities(entities)

            # Insert keywords
            keywords = []
            for keyword_text in item.get('keywords', []):
                keywords.append(KeywordRecord(
                    page_id=page_id,
                    keyword_text=keyword_text,
                    source='nlp',
                    crawl_version=self.crawl_version
                ))

            if keywords:
                self.warehouse.insert_keywords(keywords)

            # Insert categories
            categories = []
            for category_name in item.get('content_tags', []):
                categories.append(CategoryRecord(
                    page_id=page_id,
                    category_name=category_name,
                    category_path=category_name.lower().replace(' ', '_'),
                    crawl_version=self.crawl_version
                ))

            if categories:
                self.warehouse.insert_categories(categories)

            self.items_processed += 1

            if self.items_processed % 100 == 0:
                logger.info(f"Processed {self.items_processed} items to warehouse")

            return item

        except Exception as e:
            logger.error(f"Error processing item in warehouse pipeline: {e}", exc_info=True)
            return item

    def _generate_hash(self, url: str) -> str:
        """Generate SHA-256 hash of URL"""
        return hashlib.sha256(url.encode('utf-8')).hexdigest()
