"""CrawlDataManager: Analyze historical crawl data for intelligent URL prioritization.

This class provides a "mile-high view" of past crawl performance, helping the system
learn which domains, URL patterns, and content types have historically yielded valuable results.
"""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class DomainInsights:
    """Historical insights about a domain's crawl performance."""

    domain: str
    total_urls_crawled: int
    avg_content_quality: float  # 0-100
    avg_js_confidence: float  # 0-1
    successful_crawls: int
    failed_crawls: int
    success_rate: float
    avg_depth: float
    most_valuable_paths: list[str]  # Paths that yielded best content
    recommended_spider: str  # "js", "depth", "scout"


@dataclass
class URLPatternInsights:
    """Historical insights about URL pattern performance."""

    pattern: str
    match_count: int
    avg_value_score: float
    success_rate: float
    avg_content_length: int
    recommended_priority: int  # 0-100


class CrawlDataManager:
    """Analyze past crawl data from Delta Lake to inform future crawling decisions.

    This class provides high-level analytics and insights from historical crawl data,
    enabling data-driven URL prioritization and spider selection.
    """

    def __init__(self, delta_manager=None, lookback_days: int = 30):
        """Initialize CrawlDataManager.

        Args:
            delta_manager: DeltaLakeManager instance (if None, creates new one)
            lookback_days: Number of days of historical data to analyze (default: 30)
        """
        if delta_manager is None:
            from src.common.delta_lake import get_delta_manager

            self.delta_manager = get_delta_manager()
        else:
            self.delta_manager = delta_manager

        self.lookback_days = lookback_days
        self._domain_cache: dict[str, DomainInsights] = {}
        self._pattern_cache: dict[str, URLPatternInsights] = {}
        self._cache_timestamp: datetime | None = None
        self._cache_ttl_hours = 6  # Refresh cache every 6 hours

        logger.info(f"[CRAWL_DATA_MANAGER] Initialized with {lookback_days}-day lookback window")

    def _is_cache_stale(self) -> bool:
        """Check if cache needs refreshing."""
        if self._cache_timestamp is None:
            return True

        age = datetime.now() - self._cache_timestamp
        return age.total_seconds() > (self._cache_ttl_hours * 3600)

    def analyze_domain(self, domain: str, force_refresh: bool = False) -> DomainInsights | None:
        """Analyze historical performance for a specific domain.

        Args:
            domain: Domain to analyze (e.g., "uconn.edu")
            force_refresh: Force cache refresh even if cache is fresh

        Returns:
            DomainInsights object or None if no historical data
        """
        # Check cache first
        if not force_refresh and domain in self._domain_cache and not self._is_cache_stale():
            logger.debug(f"[CRAWL_DATA_MANAGER] Cache hit for domain: {domain}")
            return self._domain_cache[domain]

        try:
            # Query stage1_discovery for this domain
            cutoff_date = datetime.now() - timedelta(days=self.lookback_days)

            # Read data from Delta Lake
            discovery_data = self._read_table_safe(
                "stage1_discovery",
                columns=["url", "domain", "discovered_at", "js_confidence", "depth", "parent_url"],
            )

            # Filter by domain and date
            domain_data = [
                row
                for row in discovery_data
                if row.get("domain") == domain and self._parse_timestamp(row.get("discovered_at", "")) >= cutoff_date
            ]

            if not domain_data:
                logger.debug(f"[CRAWL_DATA_MANAGER] No historical data for domain: {domain}")
                return None

            # Calculate metrics
            total_urls = len(domain_data)
            js_confidences = [row.get("js_confidence", 0.0) for row in domain_data if row.get("js_confidence")]
            depths = [row.get("depth", 0) for row in domain_data if row.get("depth") is not None]

            avg_js_confidence = sum(js_confidences) / len(js_confidences) if js_confidences else 0.0
            avg_depth = sum(depths) / len(depths) if depths else 0.0

            # Analyze path patterns to find most valuable paths
            path_counts: dict[str, int] = defaultdict(int)
            for row in domain_data:
                url = row.get("url", "")
                if url:
                    parsed = urlparse(url)
                    # Extract first 2 path segments
                    path_parts = [p for p in parsed.path.split("/") if p][:2]
                    if path_parts:
                        path_pattern = "/" + "/".join(path_parts)
                        path_counts[path_pattern] += 1

            # Sort by frequency and take top 10
            most_valuable_paths = sorted(path_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            most_valuable_paths = [path for path, _ in most_valuable_paths]

            # Query stage1_errors for failed crawls
            error_data = self._read_table_safe("stage1_errors", columns=["url", "domain", "error_type"])
            domain_errors = [row for row in error_data if row.get("domain") == domain]

            failed_crawls = len(domain_errors)
            successful_crawls = total_urls - failed_crawls
            success_rate = successful_crawls / total_urls if total_urls > 0 else 0.0

            # Estimate content quality based on success rate and depth
            # Higher success rate + reasonable depth = higher quality
            avg_content_quality = min(100, int(success_rate * 80 + (1 - min(avg_depth / 10, 1)) * 20))

            # Recommend spider based on JS confidence
            if avg_js_confidence > 0.6:
                recommended_spider = "js"
            elif avg_depth > 3:
                recommended_spider = "depth"
            else:
                recommended_spider = "scout"

            insights = DomainInsights(
                domain=domain,
                total_urls_crawled=total_urls,
                avg_content_quality=avg_content_quality,
                avg_js_confidence=avg_js_confidence,
                successful_crawls=successful_crawls,
                failed_crawls=failed_crawls,
                success_rate=success_rate,
                avg_depth=avg_depth,
                most_valuable_paths=most_valuable_paths,
                recommended_spider=recommended_spider,
            )

            # Cache the insights
            self._domain_cache[domain] = insights
            self._cache_timestamp = datetime.now()

            logger.info(
                f"[CRAWL_DATA_MANAGER] Analyzed domain {domain}: "
                f"{total_urls} URLs, {success_rate:.1%} success rate, "
                f"quality={avg_content_quality}/100"
            )

            return insights

        except Exception as e:
            logger.error(f"[CRAWL_DATA_MANAGER] Failed to analyze domain {domain}: {e}", exc_info=True)
            return None

    def get_domain_value_boost(self, domain: str) -> int:
        """Get value boost for a domain based on historical performance.

        Args:
            domain: Domain to assess

        Returns:
            Value boost score (-20 to +30)
        """
        insights = self.analyze_domain(domain)

        if insights is None:
            # No historical data, return neutral
            return 0

        # High-quality domains get a boost
        if insights.avg_content_quality >= 80:
            return 30
        elif insights.avg_content_quality >= 60:
            return 20
        elif insights.avg_content_quality >= 40:
            return 10
        elif insights.avg_content_quality >= 20:
            return 0
        else:
            # Low-quality domains get penalized
            return -20

    def is_valuable_path(self, url: str, domain: str) -> bool:
        """Check if a URL path has historically been valuable for a domain.

        Args:
            url: URL to check
            domain: Domain of the URL

        Returns:
            True if path matches historically valuable patterns
        """
        insights = self.analyze_domain(domain)

        if insights is None or not insights.most_valuable_paths:
            return False

        parsed = urlparse(url)
        path = parsed.path.lower()

        # Check if path starts with any valuable path pattern
        for valuable_path in insights.most_valuable_paths:
            if path.startswith(valuable_path.lower()):
                return True

        return False

    def get_recommended_spider(self, domain: str) -> str | None:
        """Get recommended spider type for a domain based on historical data.

        Args:
            domain: Domain to check

        Returns:
            Spider type ("js", "depth", "scout") or None if no data
        """
        insights = self.analyze_domain(domain)

        if insights is None:
            return None

        return insights.recommended_spider

    def get_avg_js_confidence(self, domain: str) -> float:
        """Get average JS confidence for a domain from historical data.

        Args:
            domain: Domain to check

        Returns:
            Average JS confidence (0.0-1.0) or 0.0 if no data
        """
        insights = self.analyze_domain(domain)

        if insights is None:
            return 0.0

        return insights.avg_js_confidence

    def get_top_domains(self, limit: int = 20) -> list[tuple[str, DomainInsights]]:
        """Get top-performing domains by content quality.

        Args:
            limit: Maximum number of domains to return

        Returns:
            List of (domain, insights) tuples sorted by quality
        """
        try:
            # Get all unique domains from discovery table
            discovery_data = self._read_table_safe("stage1_discovery", columns=["domain"])

            unique_domains = set(row.get("domain") for row in discovery_data if row.get("domain"))

            # Analyze each domain
            domain_insights = []
            for domain in unique_domains:
                insights = self.analyze_domain(domain)
                if insights and insights.total_urls_crawled >= 10:  # Minimum threshold
                    domain_insights.append((domain, insights))

            # Sort by content quality
            domain_insights.sort(key=lambda x: x[1].avg_content_quality, reverse=True)

            return domain_insights[:limit]

        except Exception as e:
            logger.error(f"[CRAWL_DATA_MANAGER] Failed to get top domains: {e}", exc_info=True)
            return []

    def analyze_url_pattern(self, pattern: str) -> URLPatternInsights | None:
        """Analyze historical performance of URLs matching a regex pattern.

        Args:
            pattern: Regex pattern to match URLs

        Returns:
            URLPatternInsights object or None if insufficient data
        """
        # Check cache
        if pattern in self._pattern_cache and not self._is_cache_stale():
            return self._pattern_cache[pattern]

        try:
            pattern_regex = re.compile(pattern, re.IGNORECASE)

            # Read discovery data
            discovery_data = self._read_table_safe("stage1_discovery", columns=["url"])

            # Filter URLs matching pattern
            matching_urls = [row.get("url") for row in discovery_data if pattern_regex.search(row.get("url", ""))]

            if len(matching_urls) < 5:  # Minimum threshold
                return None

            # Read stage2 data to assess content quality
            stage2_data = self._read_table_safe("stage2_page_analysis", columns=["url", "content_length"])

            # Calculate metrics
            match_count = len(matching_urls)
            successful_stage2 = sum(1 for row in stage2_data if row.get("url") in matching_urls)
            success_rate = successful_stage2 / match_count if match_count > 0 else 0.0

            # Calculate average content length
            content_lengths = [row.get("content_length", 0) for row in stage2_data if row.get("url") in matching_urls]
            avg_content_length = sum(content_lengths) // len(content_lengths) if content_lengths else 0

            # Estimate value score based on success rate and content length
            avg_value_score = min(100, int(success_rate * 60 + min(avg_content_length / 1000, 40)))

            # Recommend priority based on value score
            if avg_value_score >= 70:
                recommended_priority = 80
            elif avg_value_score >= 50:
                recommended_priority = 60
            elif avg_value_score >= 30:
                recommended_priority = 40
            else:
                recommended_priority = 20

            insights = URLPatternInsights(
                pattern=pattern,
                match_count=match_count,
                avg_value_score=avg_value_score,
                success_rate=success_rate,
                avg_content_length=avg_content_length,
                recommended_priority=recommended_priority,
            )

            self._pattern_cache[pattern] = insights
            self._cache_timestamp = datetime.now()

            return insights

        except Exception as e:
            logger.error(f"[CRAWL_DATA_MANAGER] Failed to analyze pattern {pattern}: {e}", exc_info=True)
            return None

    def get_statistics(self) -> dict[str, Any]:
        """Get overall crawl statistics.

        Returns:
            Dictionary with aggregate statistics
        """
        try:
            discovery_count = self._count_table_safe("stage1_discovery")
            error_count = self._count_table_safe("stage1_errors")
            stage2_count = self._count_table_safe("stage2_page_analysis")

            # Calculate overall success rate
            total_attempts = discovery_count + error_count
            success_rate = discovery_count / total_attempts if total_attempts > 0 else 0.0

            # Get unique domains
            discovery_data = self._read_table_safe("stage1_discovery", columns=["domain"])
            unique_domains = len(set(row.get("domain") for row in discovery_data if row.get("domain")))

            return {
                "total_urls_discovered": discovery_count,
                "total_errors": error_count,
                "total_analyzed_pages": stage2_count,
                "overall_success_rate": success_rate,
                "unique_domains": unique_domains,
                "lookback_days": self.lookback_days,
                "cache_entries": len(self._domain_cache),
                "cache_age_hours": (
                    (datetime.now() - self._cache_timestamp).total_seconds() / 3600 if self._cache_timestamp else None
                ),
            }

        except Exception as e:
            logger.error(f"[CRAWL_DATA_MANAGER] Failed to get statistics: {e}", exc_info=True)
            return {}

    def clear_cache(self):
        """Clear all cached insights."""
        self._domain_cache.clear()
        self._pattern_cache.clear()
        self._cache_timestamp = None
        logger.info("[CRAWL_DATA_MANAGER] Cache cleared")

    # Helper methods for safe table operations

    def _read_table_safe(self, table_name: str, columns: list[str] | None = None) -> list[dict[str, Any]]:
        """Safely read from Delta Lake table, handling errors gracefully."""
        try:
            return self.delta_manager.read(table_name, columns=columns)
        except Exception as e:
            logger.debug(f"[CRAWL_DATA_MANAGER] Could not read table {table_name}: {e}")
            return []

    def _count_table_safe(self, table_name: str) -> int:
        """Safely count table rows, handling errors gracefully."""
        try:
            return self.delta_manager.count(table_name)
        except Exception as e:
            logger.debug(f"[CRAWL_DATA_MANAGER] Could not count table {table_name}: {e}")
            return 0

    def _parse_timestamp(self, timestamp_str: str) -> datetime:
        """Parse ISO timestamp string, handling errors gracefully."""
        try:
            # Handle timezone-aware timestamps
            if "+" in timestamp_str or timestamp_str.endswith("Z"):
                return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            else:
                return datetime.fromisoformat(timestamp_str)
        except Exception:
            # Return ancient date if parsing fails
            return datetime(1970, 1, 1)


def example_usage():
    """Example usage of CrawlDataManager."""
    manager = CrawlDataManager(lookback_days=30)

    # Analyze a specific domain
    insights = manager.analyze_domain("uconn.edu")
    if insights:
        print(f"\nDomain: {insights.domain}")
        print(f"  Total URLs crawled: {insights.total_urls_crawled}")
        print(f"  Content quality: {insights.avg_content_quality}/100")
        print(f"  Success rate: {insights.success_rate:.1%}")
        print(f"  Recommended spider: {insights.recommended_spider}")
        print(f"  Most valuable paths: {insights.most_valuable_paths[:5]}")

    # Get top domains
    print("\n\nTop 10 Domains:")
    for domain, insights in manager.get_top_domains(limit=10):
        print(f"  {domain}: quality={insights.avg_content_quality}/100, urls={insights.total_urls_crawled}")

    # Get overall statistics
    stats = manager.get_statistics()
    print("\n\nOverall Statistics:")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    example_usage()
