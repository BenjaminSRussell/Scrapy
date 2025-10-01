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
    """SQLite-based cache for pagination metadata to avoid redundant guessing"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize the SQLite database for pagination cache"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pagination (
                    pattern_hash TEXT PRIMARY KEY,
                    base_url TEXT NOT NULL,
                    last_valid_page INTEGER,
                    updated_at TEXT
                )
            """)
            conn.commit()

    def get_last_valid_page(self, base_url: str) -> int:
        """Get the last known valid page for a base URL"""
        pattern_hash = self._hash_url(base_url)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT last_valid_page FROM pagination WHERE pattern_hash = ?", (pattern_hash,))
            row = cursor.fetchone()
            return row[0] if row else 0

    def update_last_valid_page(self, base_url: str, last_valid_page: int):
        """Update the last known valid page for a base URL"""
        pattern_hash = self._hash_url(base_url)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO pagination (pattern_hash, base_url, last_valid_page, updated_at)
                VALUES (?, ?, ?, ?)
            """, (pattern_hash, base_url, last_valid_page, datetime.now().isoformat()))
            conn.commit()

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