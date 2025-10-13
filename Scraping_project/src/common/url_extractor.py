"""URL Extraction Utilities - Centralized URL discovery logic.

This module contains all URL extraction methods used by spiders to discover
links from HTML pages, scripts, sitemaps, and other sources.
"""

import base64
import logging
import re
from urllib.parse import unquote, urljoin, urlparse

from scrapy.http import Response

# Sitemap parser import removed to avoid circular dependency
# from src.stage1.sitemap_parser import SitemapParser

logger = logging.getLogger(__name__)


class URLExtractor:
    """Centralized URL extraction logic for spider use."""

    # Comprehensive URL pattern with template literal exclusions
    URL_REGEX = re.compile(
        r'(?<![{\[<$%#])(?:(?:https?|ftp):)?//[\w\-\.]+(?::\d+)?(?:/[\w\-\./?%&=]*)?'
        r'|(?<![{\[<$%#])(?:www\.)?[\w\-]+\.(?:edu|com|org|net|gov|io|co)(?:/[\w\-\./?%&=]*)?',
        re.IGNORECASE
    )

    # Patterns for encoded/obfuscated content
    ENCODED_URL_PATTERNS = [
        re.compile(r'atob\(["\']([^"\']+)["\']\)'),
        re.compile(r'decodeURIComponent\(["\']([^"\']+)["\']\)'),
        re.compile(r'unescape\(["\']([^"\']+)["\']\)'),
    ]

    # JS variables that often contain URLs
    JS_VAR_PATTERNS = [
        r'(?:var|let|const)\s+(\w+)\s*=\s*["\']([^"\']*(?:https?://|/)[^"\']+)["\']',
        r'(\w+)\s*:\s*["\']([^"\']*(?:https?://|/)[^"\']+)["\']',
        r'(?:url|href|src|endpoint|api|link)\s*[:=]\s*["\']([^"\']+)["\']',
    ]

    def __init__(self, base_url: str, allowed_domains: list[str]):
        """Initialize URL extractor with base URL and allowed domains.

        Args:
            base_url: The base URL for resolving relative URLs
            allowed_domains: List of allowed domains for filtering
        """
        self.base_url = base_url
        self.allowed_domains = allowed_domains
        self.discovered_urls: set[str] = set()

    def discover_all_urls(self, response: Response) -> set[str]:
        """Discover ALL URLs from a page using multiple extraction methods.

        Args:
            response: Scrapy Response object

        Returns:
            Set of discovered absolute URLs
        """
        self.discovered_urls = set()

        # 1. Extract from standard HTML tags
        self._extract_from_standard_tags(response)

        # 2. Extract from inline scripts
        self._extract_from_inline_scripts(response)

        # 3. Extract from external script references
        self._extract_from_script_tags(response)

        # 4. Extract from CSS (background-image, @import, etc.)
        self._extract_from_css(response)

        # 5. Extract from data attributes
        self._extract_from_data_attributes(response)

        # 6. Extract from meta tags (canonical, alternate, etc.)
        self._extract_from_meta_tags(response)

        # 7. Extract from JSON-LD structured data
        self._extract_from_json_ld(response)

        # 8. Extract from comments (sometimes URLs are commented out)
        self._extract_from_comments(response)

        # 9. Extract from onclick and other event handlers
        self._extract_from_event_handlers(response)

        # 10. Raw regex scan of entire page
        self._extract_from_raw_regex(response)

        return self.discovered_urls

    def extract_sitemap_urls(self, response: Response) -> set[str]:
        """Extract URLs from sitemaps using SitemapParser.

        Args:
            response: Scrapy Response object

        Returns:
            Set of URLs found in sitemaps
        """
        # Sitemap extraction would require separate implementation
        # Return empty set for now to avoid circular dependency
        return set()

    def _extract_from_standard_tags(self, response: Response):
        """Extract URLs from standard HTML tags (a, img, link, etc.)."""
        # Extract from <a> tags
        for href in response.css('a::attr(href)').getall():
            self._add_url(href)

        # Extract from <img> tags
        for src in response.css('img::attr(src)').getall():
            self._add_url(src)

        # Extract from <link> tags (stylesheets, icons, etc.)
        for href in response.css('link::attr(href)').getall():
            self._add_url(href)

        # Extract from <iframe> tags
        for src in response.css('iframe::attr(src)').getall():
            self._add_url(src)

        # Extract from <form> action attributes
        for action in response.css('form::attr(action)').getall():
            self._add_url(action)

        # Extract from <embed> and <object> tags
        for src in response.css('embed::attr(src), object::attr(data)').getall():
            self._add_url(src)

        # Extract from <source> tags (video/audio)
        for src in response.css('source::attr(src)').getall():
            self._add_url(src)

        # Extract from <video> and <audio> tags
        for src in response.css('video::attr(src), audio::attr(src)').getall():
            self._add_url(src)

    def _extract_from_inline_scripts(self, response: Response):
        """Extract URLs from inline JavaScript code."""
        for script in response.css('script::text').getall():
            # Look for URLs in string literals
            for match in self.URL_REGEX.finditer(script):
                self._add_url(match.group())

            # Look for JS variable patterns
            for pattern in self.JS_VAR_PATTERNS:
                for match in re.finditer(pattern, script):
                    # Extract URL from the match (last group is usually the URL)
                    url = match.groups()[-1]
                    self._add_url(url)

            # Look for encoded URLs
            for pattern in self.ENCODED_URL_PATTERNS:
                for match in pattern.finditer(script):
                    encoded = match.group(1)
                    try:
                        decoded = self._decode_url(encoded)
                        if decoded:
                            self._add_url(decoded)
                    except Exception:
                        pass

    def _extract_from_script_tags(self, response: Response):
        """Extract URLs from external script references."""
        for src in response.css('script::attr(src)').getall():
            self._add_url(src)

    def _extract_from_css(self, response: Response):
        """Extract URLs from CSS (background-image, @import, etc.)."""
        # Extract from <style> tags
        for style in response.css('style::text').getall():
            # Look for url() references
            url_pattern = re.compile(r'url\(["\']?([^"\']+)["\']?\)', re.IGNORECASE)
            for match in url_pattern.finditer(style):
                self._add_url(match.group(1))

            # Look for @import statements
            import_pattern = re.compile(r'@import\s+["\']([^"\']+)["\']', re.IGNORECASE)
            for match in import_pattern.finditer(style):
                self._add_url(match.group(1))

        # Extract from inline style attributes
        for style in response.css('[style]::attr(style)').getall():
            url_pattern = re.compile(r'url\(["\']?([^"\']+)["\']?\)', re.IGNORECASE)
            for match in url_pattern.finditer(style):
                self._add_url(match.group(1))

    def _extract_from_data_attributes(self, response: Response):
        """Extract URLs from data-* attributes."""
        # Common data attributes that contain URLs
        data_attrs = [
            'data-src', 'data-href', 'data-url', 'data-link',
            'data-image', 'data-background', 'data-lazy-src',
            'data-original', 'data-lazy', 'data-bg'
        ]

        for attr in data_attrs:
            for url in response.css(f'[{attr}]::attr({attr})').getall():
                self._add_url(url)

    def _extract_from_meta_tags(self, response: Response):
        """Extract URLs from meta tags (canonical, alternate, etc.)."""
        # Canonical URL
        for url in response.css('link[rel="canonical"]::attr(href)').getall():
            self._add_url(url)

        # Alternate URLs
        for url in response.css('link[rel="alternate"]::attr(href)').getall():
            self._add_url(url)

        # Open Graph URLs
        for url in response.css('meta[property="og:url"]::attr(content)').getall():
            self._add_url(url)

        for url in response.css('meta[property="og:image"]::attr(content)').getall():
            self._add_url(url)

        # Twitter Card URLs
        for url in response.css('meta[name="twitter:image"]::attr(content)').getall():
            self._add_url(url)

    def _extract_from_json_ld(self, response: Response):
        """Extract URLs from JSON-LD structured data."""
        import json

        for script in response.css('script[type="application/ld+json"]::text').getall():
            try:
                data = json.loads(script)
                # Recursively extract URLs from JSON structure
                self._extract_urls_from_json(data)
            except Exception:
                pass

    def _extract_urls_from_json(self, data):
        """Recursively extract URLs from JSON data."""
        if isinstance(data, dict):
            for _key, value in data.items():
                if isinstance(value, str) and ('http://' in value or 'https://' in value or value.startswith('/')):
                    self._add_url(value)
                else:
                    self._extract_urls_from_json(value)
        elif isinstance(data, list):
            for item in data:
                self._extract_urls_from_json(item)

    def _extract_from_comments(self, response: Response):
        """Extract URLs from HTML comments."""
        comment_pattern = re.compile(r'<!--(.*?)-->', re.DOTALL)
        for match in comment_pattern.finditer(response.text):
            comment = match.group(1)
            for url_match in self.URL_REGEX.finditer(comment):
                self._add_url(url_match.group())

    def _extract_from_event_handlers(self, response: Response):
        """Extract URLs from onclick and other event handlers."""
        event_attrs = ['onclick', 'onload', 'onerror', 'onmouseover', 'onfocus']

        for attr in event_attrs:
            for handler in response.css(f'[{attr}]::attr({attr})').getall():
                # Look for URLs in event handler code
                for match in self.URL_REGEX.finditer(handler):
                    self._add_url(match.group())

    def _extract_from_raw_regex(self, response: Response):
        """Perform raw regex scan of entire page for any URLs."""
        for match in self.URL_REGEX.finditer(response.text):
            url = match.group()
            # Skip obvious false positives
            if not self._is_likely_template(url):
                self._add_url(url)

    def _is_likely_template(self, url: str) -> bool:
        """Check if URL is likely a template placeholder."""
        template_indicators = [
            '{', '}', '{{', '}}',
            '${', '}',
            '<%', '%>',
            '[%', '%]',
            '__', '##',
        ]
        return any(indicator in url for indicator in template_indicators)

    def _decode_url(self, encoded: str) -> str | None:
        """Attempt to decode an encoded URL."""
        try:
            # Try base64 decode
            decoded = base64.b64decode(encoded).decode('utf-8')
            if 'http' in decoded or decoded.startswith('/'):
                return decoded
        except Exception:
            pass

        try:
            # Try URL decode
            decoded = unquote(encoded)
            if 'http' in decoded or decoded.startswith('/'):
                return decoded
        except Exception:
            pass

        return None

    def _add_url(self, url: str):
        """Add a URL to the discovered set after validation and normalization.

        Args:
            url: The URL to add (can be relative or absolute)
        """
        if not url or not isinstance(url, str):
            return

        # Clean the URL
        url = url.strip()

        # Skip empty URLs
        if not url or url == '#' or url.startswith('javascript:') or url.startswith('mailto:') or url.startswith('tel:'):
            return

        # Skip data URIs
        if url.startswith('data:'):
            return

        # Convert relative URLs to absolute
        try:
            absolute_url = urljoin(self.base_url, url)
        except Exception:
            return

        # Validate the URL
        if not self._is_valid_url(absolute_url):
            return

        # Add to discovered set
        self.discovered_urls.add(absolute_url)

    def _is_valid_url(self, url: str) -> bool:
        """Validate if a URL is properly formed and belongs to allowed domains.

        Args:
            url: The URL to validate

        Returns:
            True if URL is valid and within allowed domains
        """
        try:
            parsed = urlparse(url)

            # Must have a scheme and netloc
            if not parsed.scheme or not parsed.netloc:
                return False

            # Must be http or https
            if parsed.scheme not in ['http', 'https']:
                return False

            # Check if domain is allowed
            if self.allowed_domains:
                domain_matched = any(
                    parsed.netloc == domain or parsed.netloc.endswith('.' + domain)
                    for domain in self.allowed_domains
                )
                if not domain_matched:
                    return False

            # Skip placeholder/template domains
            placeholder_domains = ['example.com', 'example.org', 'localhost', '127.0.0.1', 'test.com', 'domain.com']
            if any(placeholder in parsed.netloc for placeholder in placeholder_domains):
                return False

            return True

        except Exception:
            return False
