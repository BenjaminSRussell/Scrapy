import hashlib
import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from scrapy.http import Response

from src.stage1.processors.url_extractor import URLExtractor
from src.common.url_value_assessor import URLValueAssessor

logger = logging.getLogger(__name__)

class URLProcessor:

    TRACKING_PARAMS = {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "msclkid",
        "ref",
        "source",
        "campaign",
        "_ga",
        "_gid",
        "_gl",
    }

    IGNORED_EXTENSIONS = {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".webp",
        ".ico",
        ".tiff",
        ".css",
        ".map",
        ".mp3",
        ".mp4",
        ".avi",
        ".mov",
        ".wmv",
        ".flv",
        ".webm",
        ".m4a",
        ".wav",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
        ".exe",
        ".dmg",
        ".pkg",
        ".deb",
        ".rpm",
    }

    DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}

    def __init__(
        self,
        base_url: str,
        allowed_domains: list[str],
        use_historical_data: bool = True,
        crawl_data_manager: Any = None,
    ):
        """
        Initialize URL processor.

        Args:
            base_url: Base URL for resolving relative URLs
            allowed_domains: List of allowed domains
            use_historical_data: Whether to use historical data for assessment
            crawl_data_manager: Optional CrawlDataManager instance
        """
        self.base_url = base_url
        self.allowed_domains = allowed_domains

        self.extractor = URLExtractor(base_url=base_url, allowed_domains=allowed_domains)
        self.assessor = URLValueAssessor(
            crawl_data_manager=crawl_data_manager,
            use_historical_data=use_historical_data,
        )

    # ============================================================================
    # ============================================================================

    def discover_and_assess(
        self,
        response: Response,
        parent_url: str | None = None,
        depth: int = 0,
        js_confidence: float = 0.0,
        min_value_score: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Discover URLs from response and assess their value.

        This is the main entry point for processing a page's URLs.

        Args:
            response: Scrapy Response object
            parent_url: URL that discovered this page
            depth: Current crawl depth
            js_confidence: JavaScript detection confidence
            min_value_score: Minimum value score to include (filter low-value URLs)

        Returns:
            List of dictionaries containing URL data:
            {
                'url': str,
                'normalized_url': str,
                'value_score': int,
                'content_likelihood': str,
                'recommended_spider': str,
                'reasons': list[str],
                'metadata': dict,
                'depth': int,
                'js_confidence': float,
            }
        """
        discovered_urls = self.extractor.discover_all_urls(response)
        logger.debug(f"Discovered {len(discovered_urls)} URLs from {response.url}")

        processed_urls = []
        for url in discovered_urls:
            normalized_url = self.normalize_url(url)
            if not normalized_url:
                continue

            if not self.should_follow_url(normalized_url):
                continue

            assessment = self.assessor.assess_url(
                url=normalized_url,
                parent_url=parent_url or response.url,
                depth=depth,
                js_confidence=js_confidence,
            )

            if assessment.value_score < min_value_score:
                continue

            url_data = {
                "url": normalized_url,
                "original_url": url,
                "value_score": assessment.value_score,
                "content_likelihood": assessment.content_likelihood,
                "recommended_spider": assessment.recommended_spider,
                "reasons": assessment.reasons,
                "metadata": assessment.metadata,
                "depth": depth + 1,
                "js_confidence": js_confidence,
                "parent_url": parent_url or response.url,
            }

            processed_urls.append(url_data)

        logger.info(f"Processed {len(processed_urls)}/{len(discovered_urls)} URLs (min_score={min_value_score})")
        return processed_urls

    # ============================================================================
    # ============================================================================

    def normalize_url(self, url: str) -> str | None:
        try:
            parsed = urlparse(url)

            if parsed.scheme not in ("http", "https"):
                return None

            if not parsed.netloc:
                return None

            scheme = parsed.scheme.lower()
            netloc = parsed.netloc.lower()

            if netloc.endswith(":80") and scheme == "http":
                netloc = netloc[:-3]
            elif netloc.endswith(":443") and scheme == "https":
                netloc = netloc[:-4]

            path = (parsed.path or "/").lower()

            if len(path) > 1 and path.endswith("/"):
                path = path.rstrip("/")

            params = ""
            if parsed.query:
                params = self._normalize_query_params(parsed.query)

            normalized = urlunparse(
                (
                    scheme,
                    netloc,
                    path,
                    "",
                    params,
                    "",
                )
            )

            return normalized

        except Exception as e:
            logger.debug(f"Failed to normalize URL {url}: {e}")
            return None

    def _normalize_query_params(self, query: str) -> str:
        try:
            params = parse_qs(query, keep_blank_values=True)

            filtered_params = {key: value for key, value in params.items() if key.lower() not in self.TRACKING_PARAMS}

            sorted_params = sorted(filtered_params.items())

            if sorted_params:
                return urlencode(sorted_params, doseq=True)
            return ""

        except Exception as e:
            logger.debug(f"Failed to normalize query params: {e}")
            return query

    # ============================================================================
    # ============================================================================

    def should_follow_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)

            path_lower = parsed.path.lower()
            for ext in self.IGNORED_EXTENSIONS:
                if path_lower.endswith(ext):
                    return False

            # NOTE: Stage 1 should capture EVERYTHING, Stage 2 will validate
            exclusion_patterns = [
                r"/wp-login\.php$",
                r"/checkout$",
            ]

            for pattern in exclusion_patterns:
                if re.search(pattern, path_lower):
                    return False

            return True

        except Exception:
            return False

    def is_document_url(self, url: str) -> bool:
        url_lower = url.lower()
        return any(url_lower.endswith(ext) for ext in self.DOCUMENT_EXTENSIONS)

    # ============================================================================
    # ============================================================================

    def hash_url(self, url: str) -> str:
        normalized = self.normalize_url(url) or url
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def deduplicate_urls(self, urls: list[str]) -> list[str]:
        seen_hashes = set()
        unique_urls = []

        for url in urls:
            url_hash = self.hash_url(url)
            if url_hash not in seen_hashes:
                seen_hashes.add(url_hash)
                unique_urls.append(url)

        logger.debug(f"Deduplicated {len(urls)} URLs to {len(unique_urls)} unique URLs")
        return unique_urls

    # ============================================================================
    # ============================================================================

    def calculate_priority(
        self,
        url: str,
        value_score: int | None = None,
        depth: int = 0,
        js_confidence: float = 0.0,
    ) -> int:
        """
        Calculate priority score for URL (for queue ordering).

        Higher scores = higher priority.

        Args:
            url: URL to prioritize
            value_score: Pre-calculated value score (if None, will assess)
            depth: Crawl depth
            js_confidence: JavaScript detection confidence

        Returns:
            Priority score (0-100)
        """
        if value_score is None:
            assessment = self.assessor.assess_url(url, depth=depth, js_confidence=js_confidence)
            value_score = assessment.value_score

        priority = value_score

        depth_penalty = min(depth * 5, 30)
        priority -= depth_penalty

        if js_confidence > 0.7:
            priority += 10

        priority = max(0, min(100, priority))

        return priority

    # ============================================================================
    # ============================================================================

    def process_batch(
        self,
        urls: list[str],
        parent_url: str | None = None,
        depth: int = 0,
        js_confidence: float = 0.0,
    ) -> list[dict[str, Any]]:
        """
        Process a batch of URLs (normalize, assess, prioritize).

        Args:
            urls: List of URLs to process
            parent_url: Parent URL that discovered these URLs
            depth: Current crawl depth
            js_confidence: JavaScript detection confidence

        Returns:
            List of processed URL data dictionaries
        """
        processed = []

        for url in urls:
            normalized = self.normalize_url(url)
            if not normalized or not self.should_follow_url(normalized):
                continue

            assessment = self.assessor.assess_url(
                url=normalized,
                parent_url=parent_url,
                depth=depth,
                js_confidence=js_confidence,
            )

            priority = self.calculate_priority(
                url=normalized,
                value_score=assessment.value_score,
                depth=depth,
                js_confidence=js_confidence,
            )

            processed.append(
                {
                    "url": normalized,
                    "value_score": assessment.value_score,
                    "priority": priority,
                    "recommended_spider": assessment.recommended_spider,
                    "depth": depth,
                    "parent_url": parent_url,
                }
            )

        return processed

# ============================================================================
# ============================================================================

def create_url_processor(base_url: str, allowed_domains: list[str]) -> URLProcessor:
    return URLProcessor(base_url=base_url, allowed_domains=allowed_domains)

def should_follow_url(url: str) -> bool:
    try:
        if not url or not isinstance(url, str):
            return False

        parsed = urlparse(url)

        if not parsed.scheme or not parsed.netloc:
            return False

        if parsed.scheme not in ("http", "https"):
            return False

        path_lower = parsed.path.lower()

        ignored_extensions = URLProcessor.IGNORED_EXTENSIONS
        for ext in ignored_extensions:
            if path_lower.endswith(ext):
                return False

        exclusion_patterns = [
            r"/wp-login\.php$",
            r"/checkout$",
        ]

        for pattern in exclusion_patterns:
            if re.search(pattern, path_lower):
                return False

        return True

    except Exception:
        return False
