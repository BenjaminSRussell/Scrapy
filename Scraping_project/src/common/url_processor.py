"""
URLProcessor - Centralized URL processing service

This module consolidates all URL-related operations into a single, coherent interface:
- URL extraction from HTML (using URLExtractor)
- URL normalization and validation
- URL prioritization and value assessment (using URLValueAssessor)
- URL filtering and deduplication

Features:
- Single entry point for all URL operations
- Consistent normalization across the application
- Intelligent prioritization for crawl efficiency
- Easy testing and mocking

Usage:
    from src.common.url_processor import URLProcessor

    processor = URLProcessor(
        base_url='https://example.com',
        allowed_domains=['example.com']
    )

    # Extract and process URLs from a response
    urls = processor.discover_and_assess(response)
    for url_data in urls:
        print(f"URL: {url_data['url']}, Score: {url_data['value_score']}")

    # Normalize a URL
    normalized = processor.normalize_url('https://example.com/page?utm_source=test')

    # Check if URL should be followed
    should_follow = processor.should_follow_url('https://example.com/login')
"""

import hashlib
import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from scrapy.http import Response

from src.common.url_extractor import URLExtractor
from src.common.url_value_assessor import URLValueAssessor

logger = logging.getLogger(__name__)


class URLProcessor:
    """
    Centralized URL processor combining extraction, normalization, and assessment.

    This class provides a unified interface for all URL operations, consolidating:
    - URLExtractor: For discovering URLs from HTML
    - URLValueAssessor: For prioritizing and assessing URL value
    - Normalization: For consistent URL formatting
    - Validation: For filtering out invalid/unwanted URLs
    """

    # Tracking parameters to remove during normalization
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

    # File extensions to skip - REDUCED to only truly useless binary/media files
    # Stage 1 liberal policy: capture everything except pure binary assets
    IGNORED_EXTENSIONS = {
        # Pure binary images (never contain links)
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".webp",
        ".ico",
        ".tiff",
        # Stylesheets and source maps (no content value)
        ".css",
        ".map",
        # Pure media files
        ".mp3",
        ".mp4",
        ".avi",
        ".mov",
        ".wmv",
        ".flv",
        ".webm",
        ".m4a",
        ".wav",
        # Fonts
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
        # Binary executables
        ".exe",
        ".dmg",
        ".pkg",
        ".deb",
        ".rpm",
        # REMOVED: .js (SPAs need these), .svg (can contain links),
        # .zip/.tar/etc (may have directory listings)
    }

    # Document extensions (valuable for processing)
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

        # Initialize sub-components
        self.extractor = URLExtractor(base_url=base_url, allowed_domains=allowed_domains)
        self.assessor = URLValueAssessor(
            crawl_data_manager=crawl_data_manager,
            use_historical_data=use_historical_data,
        )

    # ============================================================================
    # High-Level Interface
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
        # Step 1: Extract all URLs
        discovered_urls = self.extractor.discover_all_urls(response)
        logger.debug(f"Discovered {len(discovered_urls)} URLs from {response.url}")

        # Step 2: Normalize and assess each URL
        processed_urls = []
        for url in discovered_urls:
            # Normalize
            normalized_url = self.normalize_url(url)
            if not normalized_url:
                continue

            # Skip if should not follow
            if not self.should_follow_url(normalized_url):
                continue

            # Assess value
            assessment = self.assessor.assess_url(
                url=normalized_url,
                parent_url=parent_url or response.url,
                depth=depth,
                js_confidence=js_confidence,
            )

            # Filter by minimum value score
            if assessment.value_score < min_value_score:
                continue

            # Create result dictionary
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
    # URL Normalization
    # ============================================================================

    def normalize_url(self, url: str) -> str | None:
        """
        Normalize URL for consistent comparison and storage.

        Normalization includes:
        - Removing tracking parameters
        - Lowercasing scheme and domain
        - Removing default ports
        - Removing fragments
        - Sorting remaining query parameters

        Args:
            url: URL to normalize

        Returns:
            Normalized URL string, or None if invalid
        """
        try:
            parsed = urlparse(url)

            # Validate scheme
            if parsed.scheme not in ("http", "https"):
                return None

            # Validate netloc
            if not parsed.netloc:
                return None

            # Normalize scheme and netloc (lowercase)
            scheme = parsed.scheme.lower()
            netloc = parsed.netloc.lower()

            # Remove default ports
            if netloc.endswith(":80") and scheme == "http":
                netloc = netloc[:-3]
            elif netloc.endswith(":443") and scheme == "https":
                netloc = netloc[:-4]

            # Normalize path (lowercase for consistency)
            path = (parsed.path or "/").lower()

            # Remove trailing slash for non-root paths
            if len(path) > 1 and path.endswith("/"):
                path = path.rstrip("/")

            # Filter and sort query parameters
            params = ""
            if parsed.query:
                params = self._normalize_query_params(parsed.query)

            # Reconstruct URL (without fragment)
            normalized = urlunparse(
                (
                    scheme,
                    netloc,
                    path,
                    "",  # params (deprecated)
                    params,
                    "",  # fragment (removed)
                )
            )

            return normalized

        except Exception as e:
            logger.debug(f"Failed to normalize URL {url}: {e}")
            return None

    def _normalize_query_params(self, query: str) -> str:
        """
        Normalize query parameters by filtering tracking params and sorting.

        Args:
            query: Query string

        Returns:
            Normalized query string
        """
        try:
            # Parse query parameters
            params = parse_qs(query, keep_blank_values=True)

            # Filter out tracking parameters
            filtered_params = {key: value for key, value in params.items() if key.lower() not in self.TRACKING_PARAMS}

            # Sort parameters for consistency
            sorted_params = sorted(filtered_params.items())

            # Reconstruct query string
            if sorted_params:
                return urlencode(sorted_params, doseq=True)
            return ""

        except Exception as e:
            logger.debug(f"Failed to normalize query params: {e}")
            return query

    # ============================================================================
    # URL Validation and Filtering
    # ============================================================================

    def should_follow_url(self, url: str) -> bool:
        """
        Determine if a URL should be followed/crawled.

        Args:
            url: URL to check

        Returns:
            True if URL should be followed, False otherwise
        """
        try:
            parsed = urlparse(url)

            # Check file extension
            path_lower = parsed.path.lower()
            for ext in self.IGNORED_EXTENSIONS:
                if path_lower.endswith(ext):
                    return False

            # REDUCED exclusion patterns - only block truly problematic endpoints
            # NOTE: Stage 1 should capture EVERYTHING, Stage 2 will validate
            # We removed most patterns because universities often have content at:
            # - /admin/directory, /admin/faculty, etc.
            # - /login/saml, /login/shibboleth with public info
            exclusion_patterns = [
                r"/wp-login\.php$",  # Only block actual WordPress login
                r"/checkout$",  # Only block exact checkout endpoint
            ]

            for pattern in exclusion_patterns:
                if re.search(pattern, path_lower):
                    return False

            return True  # Allow everything else - we want ALL URLs in seed_urls

        except Exception:
            return False

    def is_document_url(self, url: str) -> bool:
        """
        Check if URL points to a document file.

        Args:
            url: URL to check

        Returns:
            True if URL is a document
        """
        url_lower = url.lower()
        return any(url_lower.endswith(ext) for ext in self.DOCUMENT_EXTENSIONS)

    # ============================================================================
    # URL Hashing and Deduplication
    # ============================================================================

    def hash_url(self, url: str) -> str:
        """
        Create a hash of the URL for deduplication.

        Args:
            url: URL to hash

        Returns:
            16-character hex hash
        """
        normalized = self.normalize_url(url) or url
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def deduplicate_urls(self, urls: list[str]) -> list[str]:
        """
        Remove duplicate URLs from a list.

        Args:
            urls: List of URLs

        Returns:
            List of unique URLs (deduplicated)
        """
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
    # URL Prioritization
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
        # Get value score if not provided
        if value_score is None:
            assessment = self.assessor.assess_url(url, depth=depth, js_confidence=js_confidence)
            value_score = assessment.value_score

        # Base priority from value score
        priority = value_score

        # Depth penalty (deeper = lower priority)
        depth_penalty = min(depth * 5, 30)
        priority -= depth_penalty

        # JS boost (JS-heavy pages often more valuable)
        if js_confidence > 0.7:
            priority += 10

        # Clamp to 0-100
        priority = max(0, min(100, priority))

        return priority

    # ============================================================================
    # Batch Operations
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
            # Normalize
            normalized = self.normalize_url(url)
            if not normalized or not self.should_follow_url(normalized):
                continue

            # Assess
            assessment = self.assessor.assess_url(
                url=normalized,
                parent_url=parent_url,
                depth=depth,
                js_confidence=js_confidence,
            )

            # Calculate priority
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
# Convenience Functions
# ============================================================================


def create_url_processor(base_url: str, allowed_domains: list[str]) -> URLProcessor:
    """
    Create a URLProcessor instance.

    Args:
        base_url: Base URL for resolving relative URLs
        allowed_domains: List of allowed domains

    Returns:
        URLProcessor instance
    """
    return URLProcessor(base_url=base_url, allowed_domains=allowed_domains)


def should_follow_url(url: str) -> bool:
    """
    Standalone function to check if a URL should be followed.

    This is the centralized Stage 1 URL filtering logic.
    Liberal policy: skip only pure binary/media assets that never contain links.

    Args:
        url: URL to check

    Returns:
        True if URL should be followed, False if it's a static asset to skip

    Examples:
        >>> should_follow_url("https://example.com/page.html")
        True
        >>> should_follow_url("https://example.com/image.jpg")
        False
        >>> should_follow_url("https://example.com/app.js")
        True  # JavaScript files may contain dynamic content
        >>> should_follow_url("https://example.com/doc.pdf")
        True  # Documents are valuable
    """
    try:
        # Reject empty or invalid URLs
        if not url or not isinstance(url, str):
            return False

        parsed = urlparse(url)

        # Reject URLs without valid scheme or netloc
        if not parsed.scheme or not parsed.netloc:
            return False

        # Only allow HTTP(S)
        if parsed.scheme not in ("http", "https"):
            return False

        path_lower = parsed.path.lower()

        # Check if URL ends with ignored extension
        ignored_extensions = URLProcessor.IGNORED_EXTENSIONS
        for ext in ignored_extensions:
            if path_lower.endswith(ext):
                return False

        # Minimal exclusion patterns - only block truly problematic endpoints
        exclusion_patterns = [
            r"/wp-login\.php$",  # WordPress login
            r"/checkout$",  # E-commerce checkout
        ]

        for pattern in exclusion_patterns:
            if re.search(pattern, path_lower):
                return False

        return True  # Liberal policy: allow everything else

    except Exception:
        return False
