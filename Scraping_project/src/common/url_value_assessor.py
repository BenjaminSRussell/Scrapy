"""Intelligent URL value assessment for prioritizing crawl targets."""

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class URLValue:
    """Assessment of a URL's value for crawling."""

    url: str
    value_score: int  # 0-100
    content_likelihood: str  # "high", "medium", "low"
    recommended_spider: str  # "js", "depth", "scout"
    reasons: list[str]
    metadata: dict[str, Any]


class URLValueAssessor:
    """Assess the value of URLs for intelligent crawl prioritization.

    This class evaluates URLs to determine:
    1. How valuable they are to crawl (content richness)
    2. Which spider should handle them
    3. What priority they should have
    """

    # High-value URL patterns (likely to contain rich content)
    HIGH_VALUE_PATTERNS = [
        r"/research/",
        r"/publications?/",
        r"/faculty/",
        r"/staff/",
        r"/departments?/",
        r"/programs?/",
        r"/courses?/",
        r"/academics?/",
        r"/admissions?/",
        r"/news/",
        r"/articles?/",
        r"/blog/",
        r"/events?/",
        r"/calendar/",
        r"/directory/",
        r"/resources?/",
        r"/services?/",
        r"/projects?/",
        r"/portfolio/",
        r"/about/",
        r"/contact/",
    ]

    # Low-value URL patterns (likely administrative/utility pages)
    LOW_VALUE_PATTERNS = [
        r"/login",
        r"/logout",
        r"/signin",
        r"/signout",
        r"/register",
        r"/cart",
        r"/checkout",
        r"/wp-admin",
        r"/admin/",
        r"/auth/",
        r"/account/",
        r"/print",
        r"/share",
        r"/email",
        r"/subscribe",
        r"/unsubscribe",
        r"/sitemap",
        r"/robots\.txt",
        r"/feed/",
        r"/rss/",
        r"/api/",  # Raw API endpoints (not rendered pages)
        r"/assets/",
        r"/static/",
        r"/cdn/",
    ]

    # Patterns indicating JavaScript-heavy pages
    JS_PATTERNS = [
        r"/app/",
        r"/dashboard/",
        r"/portal/",
        r"/console/",
        r"/admin/",
        r"/editor/",
        r"/viewer/",
        r"/#/",  # SPA hash routing
        r"/spa/",
    ]

    # Patterns indicating deep/hidden content
    DEPTH_PATTERNS = [
        r"/archive/",
        r"/collection/",
        r"/gallery/",
        r"/library/",
        r"/database/",
        r"/search",
        r"/browse/",
        r"/category/",
        r"/tag/",
        r"/topic/",
    ]

    # Document extensions (valuable for content extraction)
    DOCUMENT_EXTENSIONS = {
        ".pdf": 40,
        ".doc": 35,
        ".docx": 35,
        ".ppt": 30,
        ".pptx": 30,
        ".xls": 25,
        ".xlsx": 25,
    }

    def __init__(self, crawl_data_manager=None, use_historical_data: bool = True):
        """Initialize URL value assessor.

        Args:
            crawl_data_manager: CrawlDataManager instance for historical analysis (optional)
            use_historical_data: Whether to use historical data for assessment (default: True)
        """
        self.high_value_regex = re.compile("|".join(self.HIGH_VALUE_PATTERNS), re.IGNORECASE)
        self.low_value_regex = re.compile("|".join(self.LOW_VALUE_PATTERNS), re.IGNORECASE)
        self.js_regex = re.compile("|".join(self.JS_PATTERNS), re.IGNORECASE)
        self.depth_regex = re.compile("|".join(self.DEPTH_PATTERNS), re.IGNORECASE)

        self.use_historical_data = use_historical_data
        self.crawl_data_manager = crawl_data_manager

        # Lazy-load crawl data manager if enabled
        if self.use_historical_data and self.crawl_data_manager is None:
            try:
                from src.common.crawl_data_manager import CrawlDataManager

                self.crawl_data_manager = CrawlDataManager()
                logger.info("[URL_ASSESSOR] Historical data analysis enabled")
            except Exception as e:
                logger.warning(f"[URL_ASSESSOR] Could not initialize CrawlDataManager: {e}")
                self.use_historical_data = False

    def assess_url(
        self,
        url: str,
        parent_url: str | None = None,
        depth: int = 0,
        js_confidence: float = 0.0,
    ) -> URLValue:
        """Assess the value of a URL for crawling.

        Args:
            url: URL to assess
            parent_url: URL that discovered this URL
            depth: Current crawl depth
            js_confidence: JS detection confidence (0.0-1.0)

        Returns:
            URLValue assessment object
        """
        parsed = urlparse(url)
        path = parsed.path.lower()
        value_score = 50  # Base score
        reasons = []
        metadata = {}

        # Check for document extensions
        doc_boost = self._assess_document_value(url)
        if doc_boost > 0:
            value_score += doc_boost
            ext = url.lower().split(".")[-1]
            reasons.append(f"document_file_{ext}")
            metadata["is_document"] = True

        # Check historical path value first (data-driven)
        if self.use_historical_data and self.crawl_data_manager and parsed.netloc:
            try:
                domain = self._extract_domain(parsed.netloc)
                if self.crawl_data_manager.is_valuable_path(url, domain):
                    value_score += 35  # Higher boost for historically proven paths
                    reasons.append("historical_valuable_path")
                    metadata["historical_valuable_path"] = True
            except Exception as e:
                logger.debug(f"[URL_ASSESSOR] Could not check historical path value: {e}")

        # Check high-value patterns (static rules)
        if self.high_value_regex.search(path):
            value_score += 30
            reasons.append("high_value_pattern")
            metadata["has_high_value_pattern"] = True

        # Check low-value patterns
        if self.low_value_regex.search(path):
            value_score -= 40
            reasons.append("low_value_pattern")
            metadata["has_low_value_pattern"] = True

        # Assess URL structure (shorter paths often more important)
        path_segments = [p for p in path.split("/") if p]
        if len(path_segments) <= 2:
            value_score += 10
            reasons.append("short_path")
        elif len(path_segments) > 5:
            value_score -= 10
            reasons.append("deep_path")

        # Check for query parameters (may indicate dynamic content)
        if parsed.query:
            # Some query params indicate valuable filters
            if any(param in parsed.query.lower() for param in ["id=", "page=", "category=", "search="]):
                value_score += 5
                reasons.append("dynamic_params")
            else:
                # Generic tracking params reduce value
                value_score -= 5

        # Assess JS requirement
        js_boost = self._assess_js_requirement(url, js_confidence)
        if js_boost != 0:
            value_score += js_boost
            if js_boost > 0:
                reasons.append("js_heavy")
                metadata["requires_js"] = True

        # Depth penalty (deeper URLs generally less important)
        if depth > 3:
            value_score -= (depth - 3) * 5
            reasons.append(f"depth_{depth}")

        # Domain assessment
        if parsed.netloc:
            domain_boost = self._assess_domain_value(parsed.netloc)
            value_score += domain_boost
            if domain_boost > 0:
                reasons.append("valuable_domain")

        # Clamp score to 0-100
        value_score = max(0, min(100, value_score))

        # Determine content likelihood
        if value_score >= 70:
            content_likelihood = "high"
        elif value_score >= 40:
            content_likelihood = "medium"
        else:
            content_likelihood = "low"

        # Recommend spider
        recommended_spider = self._recommend_spider(url, js_confidence, value_score)

        return URLValue(
            url=url,
            value_score=value_score,
            content_likelihood=content_likelihood,
            recommended_spider=recommended_spider,
            reasons=reasons,
            metadata=metadata,
        )

    def _extract_domain(self, netloc: str) -> str:
        """Extract base domain from netloc (e.g., 'uconn.edu' from 'www.uconn.edu')."""
        domain_parts = netloc.lower().split(".")
        if len(domain_parts) >= 2:
            return ".".join(domain_parts[-2:])
        return netloc.lower()

    def _assess_document_value(self, url: str) -> int:
        """Assess value boost for document URLs."""
        url_lower = url.lower()

        for ext, boost in self.DOCUMENT_EXTENSIONS.items():
            if url_lower.endswith(ext):
                return boost

        return 0

    def _assess_js_requirement(self, url: str, js_confidence: float) -> int:
        """Assess value adjustment for JS-heavy pages."""
        # High JS confidence suggests valuable dynamic content
        if js_confidence > 0.7:
            return 20

        # JS patterns in URL
        if self.js_regex.search(url.lower()):
            return 15

        # SPA indicators
        if "/#/" in url or "/app/" in url.lower():
            return 25

        return 0

    def _assess_domain_value(self, domain: str) -> int:
        """Assess value based on domain characteristics and historical data."""
        domain_lower = domain.lower()
        base_score = 0

        # Use historical data if available
        if self.use_historical_data and self.crawl_data_manager:
            try:
                historical_boost = self.crawl_data_manager.get_domain_value_boost(domain)
                if historical_boost != 0:
                    # Historical data takes precedence
                    logger.debug(f"[URL_ASSESSOR] Historical boost for {domain}: {historical_boost}")
                    return historical_boost
            except Exception as e:
                logger.debug(f"[URL_ASSESSOR] Could not get historical data for {domain}: {e}")

        # Fallback to static rules
        # Educational domains (.edu) are often valuable
        if ".edu" in domain_lower:
            base_score = 10

        # Government domains (.gov) are often valuable
        elif ".gov" in domain_lower:
            base_score = 10

        # Subdomain depth penalty
        subdomain_count = len(domain_lower.split(".")) - 2
        if subdomain_count > 2:
            base_score -= 5

        return base_score

    def _recommend_spider(self, url: str, js_confidence: float, value_score: int) -> str:
        """Recommend which spider should handle this URL.

        Returns:
            "js" - JavaScript spider (for JS-heavy pages)
            "depth" - Depth spider (for discovery and hidden content)
            "scout" - Scout spider (for standard HTML pages)
        """
        url_lower = url.lower()

        # High JS confidence → JS spider
        if js_confidence > 0.6:
            return "js"

        # JS patterns → JS spider
        if self.js_regex.search(url_lower):
            return "js"

        # Depth/archive patterns → Depth spider
        if self.depth_regex.search(url_lower):
            return "depth"

        # Low value URLs → Skip or low-priority scout
        if value_score < 30:
            return "scout"

        # Default to scout for standard pages
        return "scout"

    def assess_batch(
        self,
        urls: list[tuple[str, dict[str, Any]]],
    ) -> list[URLValue]:
        """Assess a batch of URLs efficiently.

        Args:
            urls: List of (url, context) tuples where context contains:
                  parent_url, depth, js_confidence, etc.

        Returns:
            List of URLValue assessments
        """
        assessments = []

        for url, context in urls:
            parent_url = context.get("parent_url")
            depth = context.get("depth", 0)
            js_confidence = context.get("js_confidence", 0.0)

            assessment = self.assess_url(
                url=url,
                parent_url=parent_url,
                depth=depth,
                js_confidence=js_confidence,
            )
            assessments.append(assessment)

        return assessments

    def filter_valuable_urls(
        self,
        urls: list[str],
        min_value_score: int = 40,
    ) -> list[str]:
        """Filter URLs by minimum value score.

        Args:
            urls: List of URLs to filter
            min_value_score: Minimum value score to keep (default: 40)

        Returns:
            List of URLs meeting the value threshold
        """
        valuable_urls = []

        for url in urls:
            assessment = self.assess_url(url)
            if assessment.value_score >= min_value_score:
                valuable_urls.append(url)

        logger.info(f"[URL_ASSESSOR] Filtered {len(valuable_urls)}/{len(urls)} URLs " f"(min_score={min_value_score})")

        return valuable_urls

    def calculate_js_priority(
        self,
        js_confidence: float,
        url: str,
        framework_detected: str | None = None,
        is_spa: bool = False,
    ) -> int:
        """Calculate priority score for JavaScript rendering.

        This method centralizes all JS priority calculation logic.

        Priority levels:
        - 100: Critical (SPA, framework detected)
        - 50-75: High (high JS confidence, framework hints)
        - 25-49: Medium (moderate JS signals)
        - 0-24: Low (minimal JS)

        Args:
            js_confidence: JS detection confidence (0.0-1.0)
            url: URL to prioritize
            framework_detected: Detected framework name (React, Vue, Angular, etc.)
            is_spa: Whether page is detected as SPA

        Returns:
            Priority score (0-100)
        """
        base_priority = int(js_confidence * 50)  # 0-50 from confidence

        # Boost for frameworks
        if framework_detected:
            framework_boost = {
                "react": 50,
                "vue": 50,
                "angular": 50,
                "next": 50,
                "nuxt": 50,
                "svelte": 40,
                "ember": 40,
            }.get(framework_detected.lower(), 30)

            base_priority += framework_boost

        # Boost for SPA detection
        if is_spa:
            base_priority += 50

        # URL-based heuristics
        url_lower = url.lower()
        if any(hint in url_lower for hint in ["app", "dashboard", "portal", "console"]):
            base_priority += 10

        # Use historical data if available
        if self.use_historical_data and self.crawl_data_manager:
            try:
                parsed = urlparse(url)
                if parsed.netloc:
                    domain = self._extract_domain(parsed.netloc)
                    avg_js_conf = self.crawl_data_manager.get_avg_js_confidence(domain)
                    if avg_js_conf > 0.7:
                        # Domain historically requires JS
                        base_priority += 15
                        logger.debug(f"[URL_ASSESSOR] Historical JS boost for {domain}: +15")
            except Exception as e:
                logger.debug(f"[URL_ASSESSOR] Could not use historical JS data: {e}")

        # Cap at 100
        return min(base_priority, 100)


def example_usage():
    """Example usage of URLValueAssessor."""
    assessor = URLValueAssessor()

    # Example URLs
    test_urls = [
        "https://www.uconn.edu/research/faculty/",
        "https://www.uconn.edu/login",
        "https://portal.uconn.edu/app/dashboard/#/home",
        "https://www.uconn.edu/documents/report.pdf",
        "https://www.uconn.edu/static/assets/logo.png",
        "https://www.uconn.edu/news/article/2024/breakthrough",
    ]

    for url in test_urls:
        assessment = assessor.assess_url(url)
        print(f"\nURL: {url}")
        print(f"  Value Score: {assessment.value_score}/100")
        print(f"  Content Likelihood: {assessment.content_likelihood}")
        print(f"  Recommended Spider: {assessment.recommended_spider}")
        print(f"  Reasons: {', '.join(assessment.reasons)}")


if __name__ == "__main__":
    example_usage()
