# TODO: Add support for other storage backends, such as a database or a cloud storage service.
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class JSONLStorage:
    """Simple JSONL file storage for pipeline data"""

    def __init__(self, file_path: Path):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

    # TODO: Add support for compressing the JSONL files to save disk space.
    def append(self, data: dict[str, Any]):
        """Append a single record to the JSONL file"""
        with open(self.file_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(data, ensure_ascii=False) + '\n')

    def append_batch(self, data_list: list[dict[str, Any]]):
        """Append multiple records to the JSONL file"""
        with open(self.file_path, 'a', encoding='utf-8') as f:
            for data in data_list:
                f.write(json.dumps(data, ensure_ascii=False) + '\n')

    def read_all(self) -> Iterator[dict[str, Any]]:
        """Read all records from the JSONL file"""
        if not self.file_path.exists():
            return

        with open(self.file_path, 'r', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                try:
                    yield json.loads(line.strip())
                except json.JSONDecodeError as e:
                    logger.warning(f"Skipping invalid JSON at line {line_no}: {e}")

    def count_lines(self) -> int:
        """Count the number of lines in the file"""
        if not self.file_path.exists():
            return 0

        with open(self.file_path, 'r', encoding='utf-8') as f:
            return sum(1 for _ in f)

    def exists(self) -> bool:
        """Check if the storage file exists"""
        return self.file_path.exists()


class URLCache:
    """SQLite-based cache for URL metadata and processing status with O(1) deduplication"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize the SQLite database with optimized schema for deduplication"""
        with sqlite3.connect(self.db_path) as conn:
            # Enable WAL mode for better concurrent access
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS urls (
                    url_hash TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    discovered_at TEXT,
                    validated_at TEXT,
                    enriched_at TEXT,
                    status_code INTEGER,
                    is_valid BOOLEAN,
                    content_type TEXT,
                    title TEXT,
                    word_count INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_url_hash ON urls(url_hash)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_is_valid ON urls(is_valid)
            """)

            # Commit schema changes
            conn.commit()

    def add_discovery(self, url: str, url_hash: str, discovered_at: str):
        """Add a discovered URL to the cache"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO urls (url_hash, url, discovered_at, updated_at)
                VALUES (?, ?, ?, ?)
            """, (url_hash, url, discovered_at, datetime.now().isoformat()))

    def update_validation(self, url_hash: str, validated_at: str, status_code: int,
                         is_valid: bool, content_type: str = None):
        """Update URL with validation results"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE urls
                SET validated_at = ?, status_code = ?, is_valid = ?,
                    content_type = ?, updated_at = ?
                WHERE url_hash = ?
            """, (validated_at, status_code, is_valid, content_type,
                  datetime.now().isoformat(), url_hash))

    def update_enrichment(self, url_hash: str, enriched_at: str, title: str = None,
                         word_count: int = None):
        """Update URL with enrichment results"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE urls
                SET enriched_at = ?, title = ?, word_count = ?, updated_at = ?
                WHERE url_hash = ?
            """, (enriched_at, title, word_count, datetime.now().isoformat(), url_hash))

    def get_url(self, url_hash: str) -> dict[str, Any] | None:
        """Get URL record by hash"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM urls WHERE url_hash = ?", (url_hash,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_urls_by_status(self, is_valid: bool = None, has_enrichment: bool = None) -> list[dict[str, Any]]:
        """Get URLs filtered by processing status"""
        conditions = []
        params = []

        if is_valid is not None:
            conditions.append("is_valid = ?")
            params.append(is_valid)

        if has_enrichment is not None:
            if has_enrichment:
                conditions.append("enriched_at IS NOT NULL")
            else:
                conditions.append("enriched_at IS NULL")

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(f"SELECT * FROM urls WHERE {where_clause}", params)
            return [dict(row) for row in cursor.fetchall()]

    def get_stats(self) -> dict[str, int]:
        """Get cache statistics"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(validated_at) as validated,
                    COUNT(enriched_at) as enriched,
                    SUM(CASE WHEN is_valid = 1 THEN 1 ELSE 0 END) as valid_urls
                FROM urls
            """)
            row = cursor.fetchone()
            return {
                'total_urls': row[0],
                'validated_urls': row[1],
                'enriched_urls': row[2],
                'valid_urls': row[3]
            }

    def has_url(self, url_hash: str) -> bool:
        """Check if URL hash exists in cache - O(1) operation for deduplication"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT 1 FROM urls WHERE url_hash = ? LIMIT 1", (url_hash,))
            return cursor.fetchone() is not None

    def add_url_if_new(self, url: str, url_hash: str, discovered_at: str = None) -> bool:
        """Add URL to cache if it doesn't exist. Returns True if new, False if duplicate.

        This method provides atomic check-and-insert for O(1) deduplication.
        """
        if discovered_at is None:
            discovered_at = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute("""
                    INSERT INTO urls (url_hash, url, discovered_at, updated_at)
                    VALUES (?, ?, ?, ?)
                """, (url_hash, url, discovered_at, datetime.now().isoformat()))
                conn.commit()
                return True  # New URL
            except sqlite3.IntegrityError:
                return False  # Duplicate URL

    def get_all_hashes(self) -> set[str]:
        """Get all URL hashes from cache. Useful for bulk operations."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT url_hash FROM urls")
            return {row[0] for row in cursor.fetchall()}


class PaginationCache:
    """SQLite-based cache for pagination metadata with TTL support to avoid redundant guessing"""

    def __init__(self, db_path: Path, ttl_hours: int = 168):  # Default 7 days TTL
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl_hours = ttl_hours
        self._init_db()

    def _init_db(self):
        """Initialize the SQLite database for pagination cache with TTL"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pagination (
                    pattern_hash TEXT PRIMARY KEY,
                    base_url TEXT NOT NULL,
                    last_valid_page INTEGER,
                    max_attempted_page INTEGER DEFAULT 0,
                    attempt_count INTEGER DEFAULT 0,
                    success_rate REAL DEFAULT 0.0,
                    created_at TEXT,
                    updated_at TEXT,
                    expires_at TEXT
                )
            """)

            # Create index on expiration for efficient cleanup
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_expires_at ON pagination(expires_at)
            """)

            conn.commit()

    def get_last_valid_page(self, base_url: str) -> int:
        """Get the last known valid page for a base URL if not expired"""
        pattern_hash = self._hash_url(base_url)
        now = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT last_valid_page FROM pagination
                WHERE pattern_hash = ? AND (expires_at IS NULL OR expires_at > ?)
            """, (pattern_hash, now))
            row = cursor.fetchone()
            return row[0] if row else 0

    def update_last_valid_page(self, base_url: str, last_valid_page: int, success: bool = True):
        """Update the last known valid page with TTL and success tracking"""
        pattern_hash = self._hash_url(base_url)
        now = datetime.now()
        expires_at = (now + timedelta(hours=self.ttl_hours)).isoformat()

        with sqlite3.connect(self.db_path) as conn:
            # Get existing data if any
            cursor = conn.execute("""
                SELECT attempt_count, max_attempted_page,
                       (SELECT COUNT(*) FROM pagination WHERE pattern_hash = ?) as exists_count
                FROM pagination WHERE pattern_hash = ?
            """, (pattern_hash, pattern_hash))
            row = cursor.fetchone()

            if row and row[2] > 0:  # Record exists
                attempt_count = row[0] + 1
                max_attempted = max(row[1], last_valid_page) if row[1] else last_valid_page

                # Calculate success rate
                # Simplified: if we successfully got a page, increment successes
                success_rate = (row[0] * (row[0] / attempt_count) + (1 if success else 0)) / attempt_count if attempt_count > 0 else 0.0

                conn.execute("""
                    UPDATE pagination
                    SET last_valid_page = ?,
                        max_attempted_page = ?,
                        attempt_count = ?,
                        success_rate = ?,
                        updated_at = ?,
                        expires_at = ?
                    WHERE pattern_hash = ?
                """, (last_valid_page, max_attempted, attempt_count, success_rate,
                      now.isoformat(), expires_at, pattern_hash))
            else:
                # New record
                conn.execute("""
                    INSERT INTO pagination (
                        pattern_hash, base_url, last_valid_page, max_attempted_page,
                        attempt_count, success_rate, created_at, updated_at, expires_at
                    )
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
                """, (pattern_hash, base_url, last_valid_page, last_valid_page,
                      1.0 if success else 0.0, now.isoformat(), now.isoformat(), expires_at))

            conn.commit()

    def should_attempt_page(self, base_url: str, page_num: int, max_pages: int = 10) -> bool:
        """
        Determine if we should attempt to fetch a pagination page.

        Args:
            base_url: The base URL pattern
            page_num: Page number to attempt
            max_pages: Maximum pages to try beyond last known valid

        Returns:
            True if we should attempt this page
        """
        last_valid = self.get_last_valid_page(base_url)

        # Always attempt if we have no history
        if last_valid == 0:
            return page_num <= max_pages

        # Don't go too far beyond last known valid page
        if page_num > last_valid + max_pages:
            return False

        # Check success rate for this pattern
        pattern_hash = self._hash_url(base_url)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT success_rate FROM pagination WHERE pattern_hash = ?
            """, (pattern_hash,))
            row = cursor.fetchone()

            if row and row[0] is not None:
                # If success rate is very low, be more conservative
                if row[0] < 0.3 and page_num > last_valid + 2:
                    return False

        return True

    def cleanup_expired(self):
        """Remove expired pagination records"""
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                DELETE FROM pagination WHERE expires_at < ?
            """, (now,))
            deleted = cursor.rowcount
            conn.commit()

        if deleted > 0:
            logger.info(f"Cleaned up {deleted} expired pagination records")

        return deleted

    def _hash_url(self, url: str) -> str:
        """Generate a SHA256 hash for a URL pattern"""
        return hashlib.sha256(url.encode('utf-8')).hexdigest()


class ConfigurableStorage:
    """Configurable storage that can use JSONL or SQLite based on settings"""

    def __init__(self, storage_type: str, file_path: Path):
        self.storage_type = storage_type.lower()

        if self.storage_type == 'jsonl':
            self.storage = JSONLStorage(file_path)
        elif self.storage_type == 'sqlite':
            self.storage = URLCache(file_path)
        else:
            raise ValueError(f"Unsupported storage type: {storage_type}")

    def get_storage(self):
        return self.storage