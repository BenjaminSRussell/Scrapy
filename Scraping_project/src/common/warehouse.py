"""
Data Warehouse Connector

Provides abstraction layer for writing enriched data to both SQLite and PostgreSQL.
Handles schema creation, data insertion, and querying with proper versioning and timestamps.
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from src.common.warehouse_schema import (
    CategoryRecord,
    CrawlHistoryRecord,
    DatabaseType,
    EntityRecord,
    KeywordRecord,
    PageRecord,
    VendorDataRecord,
    get_schema_sql,
)

logger = logging.getLogger(__name__)


class DataWarehouse:
    """Data warehouse connector supporting SQLite and PostgreSQL"""

    def __init__(
        self,
        db_type: DatabaseType = DatabaseType.SQLITE,
        connection_string: str | None = None,
        auto_create_schema: bool = True
    ):
        self.db_type = db_type
        self.connection_string = connection_string

        if db_type == DatabaseType.SQLITE and not connection_string:
            # Default SQLite path
            db_path = Path("data/warehouse/uconn_warehouse.db")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.connection_string = str(db_path)

        self._connection = None
        self._postgres_connection = None

        if auto_create_schema:
            self.create_schema()

    @contextmanager
    def get_connection(self):
        """Get database connection (context manager)"""
        if self.db_type == DatabaseType.SQLITE:
            conn = sqlite3.connect(self.connection_string)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()
        else:
            # PostgreSQL connection
            try:
                import psycopg2
                import psycopg2.extras

                conn = psycopg2.connect(self.connection_string)
                try:
                    yield conn
                finally:
                    conn.close()
            except ImportError:
                raise RuntimeError("psycopg2 not installed. Run: pip install psycopg2-binary")

    def create_schema(self):
        """Create database schema"""
        schema_sql = get_schema_sql(self.db_type)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Execute schema (handles CREATE IF NOT EXISTS)
            cursor.executescript(schema_sql) if self.db_type == DatabaseType.SQLITE else cursor.execute(schema_sql)
            conn.commit()

        logger.info(f"Database schema created/verified for {self.db_type.value}")

    def insert_page(self, page: PageRecord) -> int:
        """Insert or update page record, return page_id"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Check if page exists with same url_hash
            cursor.execute(
                "SELECT page_id, crawl_version, is_current FROM pages WHERE url_hash = ? AND is_current = TRUE",
                (page.url_hash,)
            ) if self.db_type == DatabaseType.SQLITE else cursor.execute(
                "SELECT page_id, crawl_version, is_current FROM pages WHERE url_hash = %s AND is_current = TRUE",
                (page.url_hash,)
            )

            existing = cursor.fetchone()

            if existing:
                # Update existing record
                old_page_id, old_version, _ = existing if self.db_type == DatabaseType.SQLITE else (existing[0], existing[1], existing[2])

                # Mark old version as not current
                cursor.execute(
                    "UPDATE pages SET is_current = FALSE WHERE page_id = ?",
                    (old_page_id,)
                ) if self.db_type == DatabaseType.SQLITE else cursor.execute(
                    "UPDATE pages SET is_current = FALSE WHERE page_id = %s",
                    (old_page_id,)
                )

                # Insert new version
                new_version = old_version + 1
                page.crawl_version = new_version
                page.is_current = True

                # Track changes if content changed
                if self.db_type == DatabaseType.SQLITE:
                    cursor.execute(
                        "SELECT text_content, title FROM pages WHERE page_id = ?",
                        (old_page_id,)
                    )
                else:
                    cursor.execute(
                        "SELECT text_content, title FROM pages WHERE page_id = %s",
                        (old_page_id,)
                    )

                old_data = cursor.fetchone()
                if old_data:
                    old_content, old_title = old_data if self.db_type == DatabaseType.SQLITE else (old_data[0], old_data[1])

                    if old_content != page.text_content:
                        self._record_change(cursor, old_page_id, old_version, new_version, "content", old_content, page.text_content)

                    if old_title != page.title:
                        self._record_change(cursor, old_page_id, old_version, new_version, "title", old_title, page.title)

            # Insert page
            if self.db_type == DatabaseType.SQLITE:
                cursor.execute("""
                    INSERT INTO pages (
                        url, url_hash, title, text_content, word_count, content_type, status_code,
                        has_pdf_links, has_audio_links, first_seen_at, last_crawled_at,
                        crawl_version, is_current, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    page.url, page.url_hash, page.title, page.text_content, page.word_count,
                    page.content_type, page.status_code, page.has_pdf_links, page.has_audio_links,
                    page.first_seen_at, page.last_crawled_at, page.crawl_version, page.is_current,
                    page.created_at, page.updated_at
                ))
                page_id = cursor.lastrowid
            else:
                cursor.execute("""
                    INSERT INTO pages (
                        url, url_hash, title, text_content, word_count, content_type, status_code,
                        has_pdf_links, has_audio_links, first_seen_at, last_crawled_at,
                        crawl_version, is_current, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING page_id
                """, (
                    page.url, page.url_hash, page.title, page.text_content, page.word_count,
                    page.content_type, page.status_code, page.has_pdf_links, page.has_audio_links,
                    page.first_seen_at, page.last_crawled_at, page.crawl_version, page.is_current,
                    page.created_at, page.updated_at
                ))
                page_id = cursor.fetchone()[0]

            conn.commit()
            return page_id

    def _record_change(self, cursor, page_id: int, old_version: int, new_version: int, change_type: str, old_value: str, new_value: str):
        """Record page content change"""
        if self.db_type == DatabaseType.SQLITE:
            cursor.execute("""
                INSERT INTO page_changes (page_id, previous_version, current_version, change_type, old_value, new_value, changed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (page_id, old_version, new_version, change_type, old_value, new_value, datetime.now()))
        else:
            cursor.execute("""
                INSERT INTO page_changes (page_id, previous_version, current_version, change_type, old_value, new_value, changed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (page_id, old_version, new_version, change_type, old_value, new_value, datetime.now()))

    def insert_entities(self, entities: list[EntityRecord]) -> None:
        """Batch insert entities"""
        if not entities:
            return

        with self.get_connection() as conn:
            cursor = conn.cursor()

            for entity in entities:
                if self.db_type == DatabaseType.SQLITE:
                    cursor.execute("""
                        INSERT INTO entities (page_id, entity_text, entity_type, confidence, source, crawl_version, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        entity.page_id, entity.entity_text, entity.entity_type,
                        entity.confidence, entity.source, entity.crawl_version, entity.created_at
                    ))
                else:
                    cursor.execute("""
                        INSERT INTO entities (page_id, entity_text, entity_type, confidence, source, crawl_version, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        entity.page_id, entity.entity_text, entity.entity_type,
                        entity.confidence, entity.source, entity.crawl_version, entity.created_at
                    ))

            conn.commit()

    def insert_keywords(self, keywords: list[KeywordRecord]) -> None:
        """Batch insert keywords"""
        if not keywords:
            return

        with self.get_connection() as conn:
            cursor = conn.cursor()

            for keyword in keywords:
                if self.db_type == DatabaseType.SQLITE:
                    cursor.execute("""
                        INSERT INTO keywords (page_id, keyword_text, frequency, relevance_score, source, crawl_version, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        keyword.page_id, keyword.keyword_text, keyword.frequency,
                        keyword.relevance_score, keyword.source, keyword.crawl_version, keyword.created_at
                    ))
                else:
                    cursor.execute("""
                        INSERT INTO keywords (page_id, keyword_text, frequency, relevance_score, source, crawl_version, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (
                        keyword.page_id, keyword.keyword_text, keyword.frequency,
                        keyword.relevance_score, keyword.source, keyword.crawl_version, keyword.created_at
                    ))

            conn.commit()

    def insert_categories(self, categories: list[CategoryRecord]) -> None:
        """Batch insert categories"""
        if not categories:
            return

        with self.get_connection() as conn:
            cursor = conn.cursor()

            for category in categories:
                matched_kw = json.dumps(category.matched_keywords) if self.db_type == DatabaseType.SQLITE else category.matched_keywords

                if self.db_type == DatabaseType.SQLITE:
                    cursor.execute("""
                        INSERT INTO categories (page_id, category_name, category_path, confidence_score, matched_keywords, crawl_version, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        category.page_id, category.category_name, category.category_path,
                        category.confidence_score, matched_kw, category.crawl_version, category.created_at
                    ))
                else:
                    cursor.execute("""
                        INSERT INTO categories (page_id, category_name, category_path, confidence_score, matched_keywords, crawl_version, created_at)
                        VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
                    """, (
                        category.page_id, category.category_name, category.category_path,
                        category.confidence_score, json.dumps(matched_kw), category.crawl_version, category.created_at
                    ))

            conn.commit()

    def insert_crawl_history(self, crawl: CrawlHistoryRecord) -> int:
        """Insert crawl history record"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            config_json = json.dumps(crawl.config_snapshot) if crawl.config_snapshot else None

            if self.db_type == DatabaseType.SQLITE:
                cursor.execute("""
                    INSERT INTO crawl_history (
                        crawl_timestamp, stage, pages_processed, pages_successful, pages_failed,
                        duration_seconds, status, error_message, config_snapshot, created_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    crawl.crawl_timestamp, crawl.stage, crawl.pages_processed, crawl.pages_successful,
                    crawl.pages_failed, crawl.duration_seconds, crawl.status, crawl.error_message,
                    config_json, crawl.created_at, crawl.completed_at
                ))
                crawl_id = cursor.lastrowid
            else:
                cursor.execute("""
                    INSERT INTO crawl_history (
                        crawl_timestamp, stage, pages_processed, pages_successful, pages_failed,
                        duration_seconds, status, error_message, config_snapshot, created_at, completed_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    RETURNING crawl_id
                """, (
                    crawl.crawl_timestamp, crawl.stage, crawl.pages_processed, crawl.pages_successful,
                    crawl.pages_failed, crawl.duration_seconds, crawl.status, crawl.error_message,
                    config_json, crawl.created_at, crawl.completed_at
                ))
                crawl_id = cursor.fetchone()[0]

            conn.commit()
            return crawl_id

    def insert_vendor_data(self, vendor: VendorDataRecord) -> None:
        """Insert vendor/third-party data"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            raw_data_json = json.dumps(vendor.raw_data) if self.db_type == DatabaseType.SQLITE else vendor.raw_data

            if self.db_type == DatabaseType.SQLITE:
                cursor.execute("""
                    INSERT INTO vendor_data (page_id, vendor_name, vendor_url, data_type, raw_data, extracted_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    vendor.page_id, vendor.vendor_name, vendor.vendor_url,
                    vendor.data_type, raw_data_json, vendor.extracted_at
                ))
            else:
                cursor.execute("""
                    INSERT INTO vendor_data (page_id, vendor_name, vendor_url, data_type, raw_data, extracted_at)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                """, (
                    vendor.page_id, vendor.vendor_name, vendor.vendor_url,
                    vendor.data_type, json.dumps(raw_data_json), vendor.extracted_at
                ))

            conn.commit()

    def get_current_pages(self, limit: int = 100) -> Iterator[dict[str, Any]]:
        """Get current version of all pages"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            query = """
                SELECT page_id, url, url_hash, title, text_content, word_count,
                       content_type, status_code, first_seen_at, last_crawled_at, crawl_version
                FROM pages
                WHERE is_current = TRUE
                ORDER BY last_crawled_at DESC
                LIMIT ?
            """ if self.db_type == DatabaseType.SQLITE else """
                SELECT page_id, url, url_hash, title, text_content, word_count,
                       content_type, status_code, first_seen_at, last_crawled_at, crawl_version
                FROM pages
                WHERE is_current = TRUE
                ORDER BY last_crawled_at DESC
                LIMIT %s
            """

            cursor.execute(query, (limit,))

            for row in cursor.fetchall():
                if self.db_type == DatabaseType.SQLITE:
                    yield dict(row)
                else:
                    yield {
                        'page_id': row[0],
                        'url': row[1],
                        'url_hash': row[2],
                        'title': row[3],
                        'text_content': row[4],
                        'word_count': row[5],
                        'content_type': row[6],
                        'status_code': row[7],
                        'first_seen_at': row[8],
                        'last_crawled_at': row[9],
                        'crawl_version': row[10]
                    }

    def get_page_with_details(self, page_id: int) -> dict[str, Any] | None:
        """Get page with entities, keywords, and categories"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Get page
            placeholder = "?" if self.db_type == DatabaseType.SQLITE else "%s"
            cursor.execute(f"SELECT * FROM pages WHERE page_id = {placeholder}", (page_id,))
            page_row = cursor.fetchone()

            if not page_row:
                return None

            page = dict(page_row) if self.db_type == DatabaseType.SQLITE else dict(zip([desc[0] for desc in cursor.description], page_row))

            # Get entities
            cursor.execute(f"SELECT entity_text, entity_type, confidence, source FROM entities WHERE page_id = {placeholder}", (page_id,))
            page['entities'] = [dict(row) if self.db_type == DatabaseType.SQLITE else dict(zip([desc[0] for desc in cursor.description], row)) for row in cursor.fetchall()]

            # Get keywords
            cursor.execute(f"SELECT keyword_text, frequency, relevance_score, source FROM keywords WHERE page_id = {placeholder}", (page_id,))
            page['keywords'] = [dict(row) if self.db_type == DatabaseType.SQLITE else dict(zip([desc[0] for desc in cursor.description], row)) for row in cursor.fetchall()]

            # Get categories
            cursor.execute(f"SELECT category_name, category_path, confidence_score, matched_keywords FROM categories WHERE page_id = {placeholder}", (page_id,))
            page['categories'] = [dict(row) if self.db_type == DatabaseType.SQLITE else dict(zip([desc[0] for desc in cursor.description], row)) for row in cursor.fetchall()]

            return page
