"""Advanced URL extraction for finding hidden, embedded, and secret URLs."""

import json
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from scrapy.http import Response

logger = logging.getLogger(__name__)


class HiddenURLExtractor:
    """Extract hidden URLs from various sources in HTML pages.

    This class goes beyond standard link extraction to find:
    - Data attributes (data-url, data-src, data-href)
    - JSON-LD structured data
    - Embedded JavaScript configurations
    - iframe sources
    - Meta refresh redirects
    - Link headers
    - Sitemap references
    - API endpoints in JS code
    """

    # Patterns for finding URLs in JavaScript code
    JS_URL_PATTERNS = [
        # API endpoint patterns
        r'["\']/(api|v\d+)/[^"\']+["\']',
        # Fetch/AJAX calls
        r'fetch\s*\(\s*["\']([^"\']+)["\']',
        r'\.get\s*\(\s*["\']([^"\']+)["\']',
        r'\.post\s*\(\s*["\']([^"\']+)["\']',
        # URL assignments
        r'location\.href\s*=\s*["\']([^"\']+)["\']',
        r'window\.location\s*=\s*["\']([^"\']+)["\']',
        # Router configurations
        r'path:\s*["\']([^"\']+)["\']',
        r'route:\s*["\']([^"\']+)["\']',
        # Config objects
        r'baseURL:\s*["\']([^"\']+)["\']',
        r'endpoint:\s*["\']([^"\']+)["\']',
    ]

    def __init__(self, base_url: str):
        """Initialize hidden URL extractor.

        Args:
            base_url: Base URL for resolving relative URLs
        """
        self.base_url = base_url
        self.compiled_patterns = [re.compile(p, re.IGNORECASE) for p in self.JS_URL_PATTERNS]

    def extract_all_hidden_urls(self, response: Response) -> dict[str, list[str]]:
        """Extract all hidden URLs from a response.

        Returns:
            Dictionary with categorized URLs:
            {
                "data_attributes": [...],
                "json_ld": [...],
                "javascript": [...],
                "iframes": [...],
                "meta_refresh": [...],
                "sitemaps": [...],
                "api_endpoints": [...],
            }
        """
        results = {
            "data_attributes": self.extract_from_data_attributes(response),
            "json_ld": self.extract_from_json_ld(response),
            "javascript": self.extract_from_javascript(response),
            "iframes": self.extract_from_iframes(response),
            "meta_refresh": self.extract_from_meta_refresh(response),
            "sitemaps": self.extract_sitemap_urls(response),
            "api_endpoints": self.extract_api_endpoints(response),
        }

        # Log findings
        total_found = sum(len(urls) for urls in results.values())
        if total_found > 0:
            logger.info(
                f"[HIDDEN_URLS] Found {total_found} hidden URLs: {dict((k, len(v)) for k, v in results.items())}"
            )

        return results

    def extract_from_data_attributes(self, response: Response) -> list[str]:
        """Extract URLs from data-* attributes.

        Common patterns:
        - data-url
        - data-src
        - data-href
        - data-link
        - data-target
        - data-endpoint
        """
        urls = set()

        try:
            # Extract from common data attributes
            for attr in ["data-url", "data-src", "data-href", "data-link", "data-target", "data-endpoint"]:
                for url in response.css(f"[{attr}]::attr({attr})").getall():
                    if url and url.strip():
                        absolute_url = urljoin(self.base_url, url.strip())
                        if self._is_valid_url(absolute_url):
                            urls.add(absolute_url)

        except Exception as e:
            logger.debug(f"[HIDDEN_URLS] Data attribute extraction error: {e}")

        return list(urls)

    def extract_from_json_ld(self, response: Response) -> list[str]:
        """Extract URLs from JSON-LD structured data.

        JSON-LD often contains canonical URLs, breadcrumbs, and related pages.
        """
        urls = set()

        try:
            # Find all JSON-LD script tags
            json_ld_scripts = response.css('script[type="application/ld+json"]::text').getall()

            for script_content in json_ld_scripts:
                try:
                    data = json.loads(script_content)

                    # Recursively extract URLs from JSON structure
                    extracted = self._extract_urls_from_json(data)
                    urls.update(extracted)

                except json.JSONDecodeError:
                    continue

        except Exception as e:
            logger.debug(f"[HIDDEN_URLS] JSON-LD extraction error: {e}")

        return list(urls)

    def extract_from_javascript(self, response: Response) -> list[str]:
        """Extract URLs from inline JavaScript code.

        Finds:
        - API endpoints
        - Route definitions
        - Config URLs
        - Dynamic redirects
        """
        urls = set()

        try:
            # Get all script tags with inline JS
            scripts = response.css("script:not([src])::text").getall()

            for script in scripts:
                # Apply each pattern
                for pattern in self.compiled_patterns:
                    matches = pattern.findall(script)
                    for match in matches:
                        # Handle tuple results from groups
                        url = match if isinstance(match, str) else match[0]

                        # Clean and validate
                        url = url.strip().strip("'\"")
                        if url and not url.startswith(("//", "data:", "javascript:")):
                            absolute_url = urljoin(self.base_url, url)
                            if self._is_valid_url(absolute_url):
                                urls.add(absolute_url)

        except Exception as e:
            logger.debug(f"[HIDDEN_URLS] JavaScript extraction error: {e}")

        return list(urls)

    def extract_from_iframes(self, response: Response) -> list[str]:
        """Extract URLs from iframe sources."""
        urls = set()

        try:
            for src in response.css("iframe::attr(src)").getall():
                if src and src.strip():
                    absolute_url = urljoin(self.base_url, src.strip())
                    if self._is_valid_url(absolute_url):
                        urls.add(absolute_url)

        except Exception as e:
            logger.debug(f"[HIDDEN_URLS] Iframe extraction error: {e}")

        return list(urls)

    def extract_from_meta_refresh(self, response: Response) -> list[str]:
        """Extract URLs from meta refresh redirects."""
        urls = set()

        try:
            # Extract meta refresh content
            for content in response.css('meta[http-equiv="refresh"]::attr(content)').getall():
                # Format: "5; url=http://example.com"
                if "url=" in content.lower():
                    url = content.split("url=", 1)[1].strip().strip("'\"")
                    absolute_url = urljoin(self.base_url, url)
                    if self._is_valid_url(absolute_url):
                        urls.add(absolute_url)

        except Exception as e:
            logger.debug(f"[HIDDEN_URLS] Meta refresh extraction error: {e}")

        return list(urls)

    def extract_sitemap_urls(self, response: Response) -> list[str]:
        """Extract sitemap references from robots.txt and common locations."""
        urls = set()

        try:
            parsed = urlparse(self.base_url)
            base = f"{parsed.scheme}://{parsed.netloc}"

            # Common sitemap locations
            sitemap_candidates = [
                f"{base}/sitemap.xml",
                f"{base}/sitemap_index.xml",
                f"{base}/sitemap-index.xml",
                f"{base}/sitemap.txt",
                f"{base}/robots.txt",
            ]

            # Check for sitemap links in page
            for link in response.css('link[rel="sitemap"]::attr(href)').getall():
                absolute_url = urljoin(self.base_url, link)
                sitemap_candidates.append(absolute_url)

            # Add all candidates (they'll be validated by the spider)
            urls.update(sitemap_candidates)

        except Exception as e:
            logger.debug(f"[HIDDEN_URLS] Sitemap extraction error: {e}")

        return list(urls)

    def extract_api_endpoints(self, response: Response) -> list[str]:
        """Extract API endpoint patterns from JavaScript.

        Looks for RESTful API patterns like:
        - /api/v1/users
        - /v2/data/items
        - /graphql
        """
        urls = set()

        try:
            # Get all script content
            scripts = response.css("script::text").getall()
            combined_script = "\n".join(scripts)

            # API endpoint patterns
            api_patterns = [
                r'["\']/(api|v\d+)/[a-z_\-/]+["\']',
                r'["\']/(graphql|gql)["\']',
                r'["\']/(rest|restapi)/[a-z_\-/]+["\']',
            ]

            for pattern in api_patterns:
                matches = re.findall(pattern, combined_script, re.IGNORECASE)
                for match in matches:
                    endpoint = match if isinstance(match, str) else match[0]
                    endpoint = endpoint.strip().strip("'\"")

                    if endpoint:
                        absolute_url = urljoin(self.base_url, endpoint)
                        if self._is_valid_url(absolute_url):
                            urls.add(absolute_url)

        except Exception as e:
            logger.debug(f"[HIDDEN_URLS] API endpoint extraction error: {e}")

        return list(urls)

    def _extract_urls_from_json(self, data: Any, depth: int = 0) -> set[str]:
        """Recursively extract URLs from JSON structure."""
        urls: set[str] = set()

        # Prevent infinite recursion
        if depth > 10:
            return urls

        try:
            if isinstance(data, dict):
                for _key, value in data.items():
                    # Check if value looks like a URL
                    if isinstance(value, str) and self._looks_like_url(value):
                        absolute_url = urljoin(self.base_url, value)
                        if self._is_valid_url(absolute_url):
                            urls.add(absolute_url)

                    # Recurse into nested structures
                    elif isinstance(value, dict | list):
                        urls.update(self._extract_urls_from_json(value, depth + 1))

            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, str) and self._looks_like_url(item):
                        absolute_url = urljoin(self.base_url, item)
                        if self._is_valid_url(absolute_url):
                            urls.add(absolute_url)

                    elif isinstance(item, dict | list):
                        urls.update(self._extract_urls_from_json(item, depth + 1))

        except Exception as e:
            logger.debug(f"[HIDDEN_URLS] JSON URL extraction error: {e}")

        return urls

    def _looks_like_url(self, text: str) -> bool:
        """Quick check if text looks like a URL."""
        if not isinstance(text, str):
            return False

        text = text.strip()

        # Must start with protocol or /
        if not (text.startswith(("http://", "https://", "/"))):
            return False

        # Must not be a data URI or javascript
        if text.startswith(("data:", "javascript:", "mailto:")):
            return False

        # Must have reasonable length
        if len(text) > 2048 or len(text) < 2:
            return False

        return True

    def _is_valid_url(self, url: str) -> bool:
        """Validate that URL is properly formed."""
        try:
            parsed = urlparse(url)
            return bool(parsed.scheme in ("http", "https") and parsed.netloc)
        except Exception:
            return False
