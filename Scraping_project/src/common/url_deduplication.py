"""
Database-backed URL deduplication for scalable crawling.

Replaces in-memory sets with persistent SQLite storage to handle
millions of URLs without memory exhaustion.
"""

import hashlib
import logging
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class URLDeduplicator:
    """
    Persistent URL deduplication using SQLite.

    Stores URL hashes in a database instead of memory, enabling
    scalability to millions of URLs while maintaining O(1) lookups.
    """

    def __init__(self, db_path: Path, create_if_missing: bool = True):
        """
        Initialize URL deduplicator with database.

        Args:
            db_path: Path to SQLite database file
            create_if_missing: Create database if it doesn't exist
        """
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._stats = {
            'total_checked': 0,
            'duplicates_found': 0,
            'new_urls_added': 0
        }

        if create_if_missing:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()
        logger.info(f"URLDeduplicator initialized with database: {self.db_path}")

    def _init_db(self):
        """Initialize database schema with optimized indexes."""
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)

        # Enable WAL mode for better concurrent access
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute('PRAGMA synchronous=NORMAL')
        self._conn.execute('PRAGMA cache_size=-64000')  # 64MB cache

        # Create table with indexed url_hash for fast lookups
        self._conn.execute('''
            CREATE TABLE IF NOT EXISTS seen_urls (
                url_hash TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                domain TEXT NOT NULL,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create indexes for common queries
        self._conn.execute('CREATE INDEX IF NOT EXISTS idx_domain ON seen_urls(domain)')
        self._conn.execute('CREATE INDEX IF NOT EXISTS idx_first_seen ON seen_urls(first_seen)')

        self._conn.commit()

    @staticmethod
    def _hash_url(url: str) -> str:
        """
        Create consistent hash for URL.

        Uses SHA256 truncated to 16 characters for good distribution
        with reasonable storage.
        """
        return hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]

    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract domain from URL for filtering and statistics."""
        try:
            return urlparse(url).netloc.lower()
        except Exception:
            return 'unknown'

    def add_if_new(self, url: str) -> bool:
        """
        Add URL if not already seen.

        Args:
            url: URL to check and add

        Returns:
            True if URL is new and was added, False if duplicate
        """
        self._stats['total_checked'] += 1

        url_hash = self._hash_url(url)
        domain = self._extract_domain(url)

        # Check if URL exists (fast index lookup)
        cursor = self._conn.execute(
            'SELECT 1 FROM seen_urls WHERE url_hash = ?',
            (url_hash,)
        )

        if cursor.fetchone():
            self._stats['duplicates_found'] += 1
            return False  # Already seen

        # Insert new URL
        try:
            self._conn.execute(
                'INSERT INTO seen_urls (url_hash, url, domain) VALUES (?, ?, ?)',
                (url_hash, url, domain)
            )
            self._conn.commit()
            self._stats['new_urls_added'] += 1
            return True  # New URL

        except sqlite3.IntegrityError:
            # Race condition - another thread added it
            self._stats['duplicates_found'] += 1
            return False

    def has_seen(self, url: str) -> bool:
        """
        Check if URL has been seen before.

        Args:
            url: URL to check

        Returns:
            True if URL exists in database
        """
        url_hash = self._hash_url(url)
        cursor = self._conn.execute(
            'SELECT 1 FROM seen_urls WHERE url_hash = ?',
            (url_hash,)
        )
        return cursor.fetchone() is not None

    def count(self) -> int:
        """Get total number of unique URLs seen."""
        cursor = self._conn.execute('SELECT COUNT(*) FROM seen_urls')
        return cursor.fetchone()[0]

    def count_by_domain(self, domain: str) -> int:
        """Get count of URLs for specific domain."""
        cursor = self._conn.execute(
            'SELECT COUNT(*) FROM seen_urls WHERE domain = ?',
            (domain,)
        )
        return cursor.fetchone()[0]

    def get_domains(self) -> list[tuple[str, int]]:
        """
        Get all domains with URL counts.

        Returns:
            List of (domain, count) tuples, sorted by count descending
        """
        cursor = self._conn.execute('''
            SELECT domain, COUNT(*) as count
            FROM seen_urls
            GROUP BY domain
            ORDER BY count DESC
        ''')
        return cursor.fetchall()

    def get_stats(self) -> dict:
        """
        Get deduplication statistics.

        Returns:
            Dictionary with statistics
        """
        return {
            'total_checked': self._stats['total_checked'],
            'duplicates_found': self._stats['duplicates_found'],
            'new_urls_added': self._stats['new_urls_added'],
            'unique_urls_in_db': self.count(),
            'duplicate_rate': self._stats['duplicates_found'] / max(self._stats['total_checked'], 1)
        }

    def clear(self):
        """Clear all URLs from database. Use with caution!"""
        self._conn.execute('DELETE FROM seen_urls')
        self._conn.commit()
        self._stats = {
            'total_checked': 0,
            'duplicates_found': 0,
            'new_urls_added': 0
        }
        logger.warning("URLDeduplicator database cleared")

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        """Context manager support."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager support."""
        self.close()

    def __del__(self):
        """Ensure database is closed on deletion."""
        self.close()
