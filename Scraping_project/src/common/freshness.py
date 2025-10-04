"""
Freshness Tracking and Staleness Scoring

Tracks Last-Modified, ETag, and content change patterns to:
- Calculate staleness scores
- Prioritize frequently-changing content
- Track per-domain content churn rates
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

from src.common.logging import get_structured_logger

logger = get_structured_logger(__name__, component="freshness_tracker")


@dataclass
class FreshnessRecord:
    """Freshness record for a URL"""
    url: str
    url_hash: str
    last_modified: str | None = None
    etag: str | None = None
    last_validated: str | None = None
    validation_count: int = 0
    content_changed_count: int = 0
    staleness_score: float = 0.0


class FreshnessTracker:
    """
    Track URL freshness and calculate staleness scores.

    Staleness score calculation:
    - Age since last modification (0-40%)
    - Change frequency (0-30%)
    - Content type heuristics (0-30%)
    """

    def __init__(self, db_path: Path | None = None):
        """
        Initialize freshness tracker.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path or Path("data/cache/freshness.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()

        # Domain churn tracking (in-memory)
        self.domain_churn_stats: dict[str, dict] = {}

    def _init_db(self):
        """Initialize database schema"""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS freshness_records (
                    url_hash TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    last_modified TEXT,
                    etag TEXT,
                    last_validated TEXT,
                    validation_count INTEGER DEFAULT 0,
                    content_changed_count INTEGER DEFAULT 0,
                    staleness_score REAL DEFAULT 0.0,
                    domain TEXT,
                    content_type TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_freshness_domain
                ON freshness_records(domain)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_freshness_staleness
                ON freshness_records(staleness_score)
            """)

            conn.commit()

    def update_freshness(
        self,
        url: str,
        url_hash: str,
        last_modified: str | None = None,
        etag: str | None = None,
        content_type: str | None = None,
        content_changed: bool = False
    ) -> float:
        """
        Update freshness record and calculate staleness score.

        Args:
            url: URL
            url_hash: URL hash
            last_modified: Last-Modified header value
            etag: ETag header value
            content_type: Content-Type
            content_changed: Whether content changed since last check

        Returns:
            Staleness score (0.0=fresh, 1.0=very stale)
        """
        now = datetime.now().isoformat()
        domain = urlparse(url).netloc

        # Get existing record
        existing = self.get_freshness_record(url_hash)

        if existing:
            validation_count = existing.validation_count + 1
            content_changed_count = existing.content_changed_count + (1 if content_changed else 0)
        else:
            validation_count = 1
            content_changed_count = 1 if content_changed else 0

        # Calculate staleness score
        staleness_score = self._calculate_staleness_score(
            url=url,
            last_modified=last_modified,
            etag=etag,
            content_type=content_type,
            validation_count=validation_count,
            content_changed_count=content_changed_count
        )

        # Persist to database
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO freshness_records
                (url_hash, url, last_modified, etag, last_validated,
                 validation_count, content_changed_count, staleness_score,
                 domain, content_type, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                url_hash, url, last_modified, etag, now,
                validation_count, content_changed_count, staleness_score,
                domain, content_type, now
            ))
            conn.commit()

        # Update domain churn stats
        self._update_domain_churn(domain, content_changed)

        logger.debug(f"Freshness updated: {url_hash[:12]}, score={staleness_score:.3f}")

        return staleness_score

    def _calculate_staleness_score(
        self,
        url: str,
        last_modified: str | None,
        etag: str | None,
        content_type: str | None,
        validation_count: int,
        content_changed_count: int
    ) -> float:
        """
        Calculate staleness score (0.0-1.0).

        Components:
        - Age score (0-40%): Time since last modification
        - Change frequency (0-30%): How often content changes
        - Content type heuristics (0-30%): News/events vs static pages
        """
        score = 0.0

        # Age score (0-0.4)
        if last_modified:
            try:
                last_mod_dt = parsedate_to_datetime(last_modified)
                age_days = (datetime.now(last_mod_dt.tzinfo) - last_mod_dt).days

                if age_days == 0:
                    score += 0.0  # Fresh
                elif age_days <= 7:
                    score += 0.1
                elif age_days <= 30:
                    score += 0.2
                elif age_days <= 90:
                    score += 0.3
                else:
                    score += 0.4  # Stale
            except Exception as e:
                logger.debug(f"Failed to parse Last-Modified: {e}")
                score += 0.2  # Assume moderate age

        else:
            # No Last-Modified header = assume moderate age
            score += 0.2

        # Change frequency score (0-0.3)
        if validation_count > 0:
            change_rate = content_changed_count / validation_count

            if change_rate >= 0.5:
                score += 0.0  # High churn = fresh
            elif change_rate >= 0.2:
                score += 0.1
            elif change_rate >= 0.05:
                score += 0.2
            else:
                score += 0.3  # Low churn = stale

        # Content type heuristics (0-0.3)
        url_lower = url.lower()
        content_type_lower = (content_type or "").lower()

        # High-churn patterns (reduce staleness)
        if any(term in url_lower for term in ['/news/', '/events/', '/blog/', '/announcements/']):
            score += 0.0
        # Medium-churn patterns
        elif any(term in url_lower for term in ['/research/', '/publications/', '/faculty/']):
            score += 0.1
        # Low-churn patterns
        elif any(term in url_lower for term in ['/about/', '/contact/', '/history/']):
            score += 0.3
        # Static content
        elif any(ct in content_type_lower for ct in ['image/', 'video/', 'audio/', 'application/pdf']):
            score += 0.3
        else:
            score += 0.15  # Default

        return min(1.0, max(0.0, score))

    def get_freshness_record(self, url_hash: str) -> FreshnessRecord | None:
        """Get freshness record for URL"""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute("""
                SELECT url, url_hash, last_modified, etag, last_validated,
                       validation_count, content_changed_count, staleness_score
                FROM freshness_records
                WHERE url_hash = ?
            """, (url_hash,))

            row = cursor.fetchone()
            if row:
                return FreshnessRecord(
                    url=row[0],
                    url_hash=row[1],
                    last_modified=row[2],
                    etag=row[3],
                    last_validated=row[4],
                    validation_count=row[5],
                    content_changed_count=row[6],
                    staleness_score=row[7]
                )

        return None

    def _update_domain_churn(self, domain: str, content_changed: bool):
        """Update domain churn statistics"""
        if domain not in self.domain_churn_stats:
            self.domain_churn_stats[domain] = {
                'total_checks': 0,
                'changes_detected': 0,
                'churn_rate': 0.0
            }

        stats = self.domain_churn_stats[domain]
        stats['total_checks'] += 1
        if content_changed:
            stats['changes_detected'] += 1

        stats['churn_rate'] = stats['changes_detected'] / stats['total_checks']

    def get_domain_churn_metrics(self) -> dict[str, dict]:
        """Get per-domain content churn metrics for Prometheus"""
        return self.domain_churn_stats.copy()

    def should_revalidate(
        self,
        url_hash: str,
        min_freshness_hours: int = 24
    ) -> bool:
        """
        Check if URL should be revalidated based on staleness.

        Args:
            url_hash: URL hash
            min_freshness_hours: Minimum hours before revalidation

        Returns:
            True if should revalidate
        """
        record = self.get_freshness_record(url_hash)

        if not record or not record.last_validated:
            return True  # Never validated

        try:
            last_val = datetime.fromisoformat(record.last_validated)
            age_hours = (datetime.now() - last_val).total_seconds() / 3600

            # High staleness = revalidate sooner
            threshold_hours = min_freshness_hours * (1.0 - record.staleness_score)

            return age_hours >= threshold_hours

        except Exception:
            return True  # On error, revalidate
