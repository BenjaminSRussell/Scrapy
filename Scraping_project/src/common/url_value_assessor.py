"""Intelligent URL value assessment for prioritizing crawl targets."""

import logging
import math
import posixpath
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import urlparse

if TYPE_CHECKING:
    from src.common.crawl_data_manager import CrawlDataManager

logger = logging.getLogger(__name__)

# URL hints for JS-heavy apps, matched as whole host labels / path segments so
# "apply", "happy" or "reapportionment" no longer count as "app" (#267).
_JS_URL_HINT = re.compile(r"(?:^|[/.\-_#])(?:app|apps|dashboard|portal|console)(?:$|[/.\-_?#])", re.IGNORECASE)


def _clamp01(value: Any) -> float:
    """Coerce a confidence to [0.0, 1.0]; None/NaN/garbage count as 0."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(v):
        return 0.0
    return min(max(v, 0.0), 1.0)


def _host_suffix_is(host: str, tld: str) -> bool:
    """True if the host's last label is ``tld`` (``x.edu``), not a substring (``education.com``)."""
    return host.rstrip(".").lower().rsplit(".", 1)[-1] == tld

@dataclass
class URLValue:

    url: str
    value_score: int
    content_likelihood: str
    recommended_spider: str
    reasons: list[str]
    metadata: dict[str, Any]

class URLValueAssessor:

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
        r"/api/",
        r"/assets/",
        r"/static/",
        r"/cdn/",
    ]

    JS_PATTERNS = [
        r"/app/",
        r"/dashboard/",
        r"/portal/",
        r"/console/",
        r"/admin/",
        r"/editor/",
        r"/viewer/",
        r"/#/",
        r"/spa/",
    ]

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

    DOCUMENT_EXTENSIONS = {
        ".pdf": 40,
        ".doc": 35,
        ".docx": 35,
        ".ppt": 30,
        ".pptx": 30,
        ".xls": 25,
        ".xlsx": 25,
    }

    def __init__(
        self,
        crawl_data_manager: Optional["CrawlDataManager"] = None,
        use_historical_data: bool = True,
    ):
        self.high_value_regex = re.compile("|".join(self.HIGH_VALUE_PATTERNS), re.IGNORECASE)
        self.low_value_regex = re.compile("|".join(self.LOW_VALUE_PATTERNS), re.IGNORECASE)
        self.js_regex = re.compile("|".join(self.JS_PATTERNS), re.IGNORECASE)
        self.depth_regex = re.compile("|".join(self.DEPTH_PATTERNS), re.IGNORECASE)

        self.use_historical_data = use_historical_data
        self.crawl_data_manager = crawl_data_manager

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
        value_score = 50
        reasons = []
        metadata = {}
        js_confidence = _clamp01(js_confidence)
        try:
            depth = max(0, int(depth))
        except (TypeError, ValueError):
            depth = 0

        doc_boost = self._assess_document_value(url)
        if doc_boost > 0:
            value_score += doc_boost
            ext = posixpath.splitext(path)[1].lstrip(".")
            reasons.append(f"document_file_{ext}")
            metadata["is_document"] = True

        if self.use_historical_data and self.crawl_data_manager and parsed.netloc:
            try:
                domain = self._extract_domain(parsed.netloc)
                if self.crawl_data_manager.is_valuable_path(url, domain):
                    value_score += 35
                    reasons.append("historical_valuable_path")
                    metadata["historical_valuable_path"] = True
            except Exception as e:
                logger.debug(f"[URL_ASSESSOR] Could not check historical path value: {e}")

        if self.high_value_regex.search(path):
            value_score += 30
            reasons.append("high_value_pattern")
            metadata["has_high_value_pattern"] = True

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

        if parsed.query:
            if any(param in parsed.query.lower() for param in ["id=", "page=", "category=", "search="]):
                value_score += 5
                reasons.append("dynamic_params")
            else:
                value_score -= 5

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

        if parsed.netloc:
            domain_boost = self._assess_domain_value(parsed.netloc)
            value_score += domain_boost
            if domain_boost > 0:
                reasons.append("valuable_domain")

        value_score = max(0, min(100, value_score))

        if value_score >= 70:
            content_likelihood = "high"
        elif value_score >= 40:
            content_likelihood = "medium"
        else:
            content_likelihood = "low"

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
        # Public-suffix aware: "news.bbc.co.uk" -> "bbc.co.uk" (was "co.uk"),
        # and ports/userinfo are dropped. Matches the Delta "domain" column.
        from src.utils.validation import registrable_domain

        return registrable_domain(netloc.rsplit("@", 1)[-1])

    def _assess_document_value(self, url: str) -> int:
        # Look at the path only: "report.pdf?download=1" is still a PDF and
        # "/view?file=x.pdf" is not one.
        try:
            path = urlparse(url).path.lower()
        except ValueError:
            return 0
        return self.DOCUMENT_EXTENSIONS.get(posixpath.splitext(path)[1], 0)

    def _assess_js_requirement(self, url: str, js_confidence: float) -> int:
        js_confidence = _clamp01(js_confidence)
        if js_confidence > 0.7:
            return 20

        if self.js_regex.search(url.lower()):
            return 15

        if "/#/" in url or "/app/" in url.lower():
            return 25

        return 0

    def _assess_domain_value(self, domain: str) -> int:
        domain_lower = domain.lower()
        base_score = 0

        if self.use_historical_data and self.crawl_data_manager:
            try:
                historical_boost = self.crawl_data_manager.get_domain_value_boost(domain)
                if historical_boost != 0:
                    logger.debug(f"[URL_ASSESSOR] Historical boost for {domain}: {historical_boost}")
                    return historical_boost
            except Exception as e:
                logger.debug(f"[URL_ASSESSOR] Could not get historical data for {domain}: {e}")

        host = domain_lower.rsplit("@", 1)[-1].split(":", 1)[0]
        if _host_suffix_is(host, "edu") or _host_suffix_is(host, "gov"):
            base_score = 10

        subdomain_count = len(host.rstrip(".").split(".")) - 2
        if subdomain_count > 2:
            base_score -= 5

        return base_score

    def _recommend_spider(self, url: str, js_confidence: float, value_score: int) -> str:
        url_lower = url.lower()
        js_confidence = _clamp01(js_confidence)

        if js_confidence > 0.6:
            return "js"

        if self.js_regex.search(url_lower):
            return "js"

        if self.depth_regex.search(url_lower):
            return "depth"

        if value_score < 30:
            return "scout"

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

        logger.info(f"[URL_ASSESSOR] Filtered {len(valuable_urls)}/{len(urls)} URLs (min_score={min_value_score})")

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
        base_priority = int(_clamp01(js_confidence) * 50)

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

        if is_spa:
            base_priority += 50

        try:
            parsed_hint = urlparse(url)
            hint_target = f"{parsed_hint.hostname or ''}{parsed_hint.path}"
        except ValueError:
            hint_target = url
        if _JS_URL_HINT.search(hint_target):
            base_priority += 10

        if self.use_historical_data and self.crawl_data_manager:
            try:
                parsed = urlparse(url)
                if parsed.netloc:
                    domain = self._extract_domain(parsed.netloc)
                    avg_js_conf = self.crawl_data_manager.get_avg_js_confidence(domain)
                    if avg_js_conf > 0.7:
                        base_priority += 15
                        logger.debug(f"[URL_ASSESSOR] Historical JS boost for {domain}: +15")
            except Exception as e:
                logger.debug(f"[URL_ASSESSOR] Could not use historical JS data: {e}")

        return max(0, min(base_priority, 100))

def example_usage():
    assessor = URLValueAssessor()

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
