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

from pybloom_live import BloomFilter

logger = logging.getLogger(__name__)


class URLDeduplicator:
    """
    Persistent URL deduplication using SQLite and a Bloom Filter.

    Stores URL hashes in a database for persistence and uses a Bloom Filter
    for rapid, memory-efficient checking of previously seen URLs.
    """

    def __init__(self, db_path: Path, create_if_missing: bool = True,
                 bloom_capacity: int = 1000000, bloom_error_rate: float = 0.001):
        """
        Initialize URL deduplicator with database and Bloom Filter.

        Args:
            db_path: Path to SQLite database file.
            create_if_missing: Create database if it doesn't exist.
            bloom_capacity: The anticipated number of items to be added to the bloom filter.
            bloom_error_rate: The desired false positive rate.
        """
        self.db_path = Path(db_path)
        self.bloom_filter_path = self.db_path.with_suffix('.bloom')
        self._conn: sqlite3.Connection | None = None
        self._stats = {
            'total_checked': 0,
            'duplicates_found': 0,
            'new_urls_added': 0,
            'bloom_filter_hits': 0
        }

        if create_if_missing:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()
        self._init_bloom_filter(bloom_capacity, bloom_error_rate)
        logger.info(f"URLDeduplicator initialized with database: {self.db_path}")

    def _init_db(self):
        """Initialize database schema with optimized indexes."""
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute('PRAGMA synchronous=NORMAL')
        self._conn.execute('PRAGMA cache_size=-64000')  # 64MB cache
        self._conn.execute('''
            CREATE TABLE IF NOT EXISTS seen_urls (
                url_hash TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                domain TEXT NOT NULL,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self._conn.execute('CREATE INDEX IF NOT EXISTS idx_domain ON seen_urls(domain)')
        self._conn.execute('CREATE INDEX IF NOT EXISTS idx_first_seen ON seen_urls(first_seen)')
        self._conn.commit()

    def _init_bloom_filter(self, capacity: int, error_rate: float):
        """Initialize the Bloom Filter, loading from file if it exists."""
        if self.bloom_filter_path.exists():
            try:
                with open(self.bloom_filter_path, 'rb') as f:
                    self.bloom_filter = BloomFilter.fromfile(f)
                logger.info(f"Loaded Bloom Filter from {self.bloom_filter_path}")
            except Exception as e:
                logger.error(f"Could not load Bloom Filter from {self.bloom_filter_path}: {e}. Creating a new one.")
                self.bloom_filter = BloomFilter(capacity=capacity, error_rate=error_rate)
        else:
            self.bloom_filter = BloomFilter(capacity=capacity, error_rate=error_rate)
            logger.info("Created a new Bloom Filter.")

        # Populate bloom filter from DB if it's empty and DB is not
        if not self.bloom_filter and self.count() > 0:
            logger.info("Populating Bloom Filter from existing database...")
            cursor = self._conn.execute('SELECT url_hash FROM seen_urls')
            for row in cursor:
                self.bloom_filter.add(row[0])
            logger.info(f"Bloom Filter populated with {len(self.bloom_filter)} items.")


    def save_bloom_filter(self):
        """Save the Bloom Filter to a file."""
        try:
            with open(self.bloom_filter_path, 'wb') as f:
                self.bloom_filter.tofile(f)
            logger.info(f"Bloom Filter saved to {self.bloom_filter_path}")
        except Exception as e:
            logger.error(f"Could not save Bloom Filter to {self.bloom_filter_path}: {e}")


    @staticmethod
    def _hash_url(url: str) -> str:
        """Create consistent hash for URL."""
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
        Add URL if not already seen, using the Bloom Filter for a quick check.

        Args:
            url: URL to check and add.

        Returns:
            True if URL is new and was added, False if duplicate.
        """
        self._stats['total_checked'] += 1
        url_hash = self._hash_url(url)

        # First, check the Bloom Filter
        if url_hash in self.bloom_filter:
            self._stats['bloom_filter_hits'] += 1
            # It's likely in the DB, so we do a real check
            cursor = self._conn.execute('SELECT 1 FROM seen_urls WHERE url_hash = ?', (url_hash,))
            if cursor.fetchone():
                self._stats['duplicates_found'] += 1
                return False  # Confirmed duplicate

        # If not in bloom filter or it was a false positive, it's likely new.
        # Try to insert into the database.
        domain = self._extract_domain(url)
        try:
            self._conn.execute(
                'INSERT INTO seen_urls (url_hash, url, domain) VALUES (?, ?, ?)',
                (url_hash, url, domain)
            )
            self._conn.commit()
            self.bloom_filter.add(url_hash)  # Add to bloom filter
            self._stats['new_urls_added'] += 1
            return True  # New URL
        except sqlite3.IntegrityError:
            # Race condition or bloom filter false negative
            self._stats['duplicates_found'] += 1
            # Make sure it's in the bloom filter for next time
            if url_hash not in self.bloom_filter:
                 self.bloom_filter.add(url_hash)
            return False

    def has_seen(self, url: str) -> bool:
        """
        Check if URL has been seen before, using the Bloom Filter.

        Args:
            url: URL to check.

        Returns:
            True if URL likely exists in the database.
        """
        url_hash = self._hash_url(url)
        if url_hash in self.bloom_filter:
            self._stats['bloom_filter_hits'] += 1
            return self.has_seen_in_db(url_hash)
        return False

    def has_seen_in_db(self, url_hash: str) -> bool:
        """Directly check the database for a URL hash."""
        cursor = self._conn.execute('SELECT 1 FROM seen_urls WHERE url_hash = ?', (url_hash,))
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
            List of (domain, count) tuples, sorted by count descending.
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
            Dictionary with statistics.
        """
        return {
            'total_checked': self._stats['total_checked'],
            'duplicates_found': self._stats['duplicates_found'],
            'new_urls_added': self._stats['new_urls_added'],
            'unique_urls_in_db': self.count(),
            'bloom_filter_hits': self._stats['bloom_filter_hits'],
            'duplicate_rate': self._stats['duplicates_found'] / max(self._stats['total_checked'], 1)
        }

    def clear(self):
        """Clear all URLs from database and bloom filter. Use with caution!"""
        self._conn.execute('DELETE FROM seen_urls')
        self._conn.commit()
        self.bloom_filter.clear()
        self._stats = {
            'total_checked': 0,
            'duplicates_found': 0,
            'new_urls_added': 0,
            'bloom_filter_hits': 0
        }
        logger.warning("URLDeduplicator database and Bloom Filter cleared")

    def close(self):
        """Save the bloom filter and close the database connection."""
        self.save_bloom_filter()
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
