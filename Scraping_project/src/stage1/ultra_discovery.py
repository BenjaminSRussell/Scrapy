"""Ultra-Aggressive URL Discovery Methods
Ensures NO URL is missed - extracts from every possible source
"""

import base64
import json
import re
from collections.abc import Iterator
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from scrapy.http import Response


class UltraDiscovery:
    """Extreme URL discovery - extracts URLs from every conceivable source.
    Use when you absolutely cannot miss ANY URL.
    """

    # Comprehensive URL pattern - matches various formats
    URL_REGEX = re.compile(
        r'(?:(?:https?|ftp):)?//[\w\-\.]+(?::\d+)?(?:/[\w\-\./?%&=]*)?'
        r'|(?:www\.)?[\w\-]+\.(?:edu|com|org|net|gov|io|co)(?:/[\w\-\./?%&=]*)?',
        re.IGNORECASE
    )

    # Patterns for finding URLs in encoded/obfuscated content
    ENCODED_URL_PATTERNS = [
        re.compile(r'atob\(["\']([^"\']+)["\']\)'),  # Base64 in JavaScript
        re.compile(r'decodeURIComponent\(["\']([^"\']+)["\']\)'),  # URL encoded
        re.compile(r'unescape\(["\']([^"\']+)["\']\)'),  # Escaped
    ]

    # JavaScript variable patterns that often contain URLs
    JS_VAR_PATTERNS = [
        r'(?:var|let|const)\s+(\w+)\s*=\s*["\']([^"\']*(?:https?://|/)[^"\']+)["\']',
        r'(\w+)\s*:\s*["\']([^"\']*(?:https?://|/)[^"\']+)["\']',  # Object properties
        r'(?:url|href|src|endpoint|api|link)\s*[:=]\s*["\']([^"\']+)["\']',
    ]

    def __init__(self, response: Response):
        self.response = response
        self.base_url = response.url
        self.discovered_urls = set()

    def discover_all(self) -> Iterator[str]:
        """Extract URLs from ALL possible sources"""
        # 1. Standard extraction
        yield from self._extract_from_standard_tags()

        # 2. JavaScript sources
        yield from self._extract_from_javascript()
        yield from self._extract_from_inline_scripts()
        yield from self._extract_from_event_handlers()

        # 3. CSS sources
        yield from self._extract_from_css()
        yield from self._extract_from_style_tags()

        # 4. Meta tags
        yield from self._extract_from_meta_tags()

        # 5. Iframes and embeds
        yield from self._extract_from_iframes()

        # 6. Data URIs and Base64
        yield from self._extract_from_data_uris()

        # 7. JSON-LD structured data
        yield from self._extract_from_json_ld()

        # 8. Microdata and RDFa
        yield from self._extract_from_microdata()

        # 9. srcset and picture elements
        yield from self._extract_from_srcset()

        # 10. Link headers
        yield from self._extract_from_headers()

        # 11. Obfuscated/encoded URLs
        yield from self._extract_from_encoded()

        # 12. Query parameters (nested URLs)
        yield from self._extract_from_query_params()

        # 13. Path segments (potential endpoints)
        yield from self._generate_from_patterns()

        # Return unique URLs
        yield from self.discovered_urls

    def _extract_from_standard_tags(self) -> Iterator[str]:
        """Extract from standard HTML tags"""
        # Links
        for href in self.response.css('a::attr(href), area::attr(href)').getall():
            self._add_url(href)

        # Images and media
        for src in self.response.css('img::attr(src), source::attr(src), track::attr(src)').getall():
            self._add_url(src)

        # Scripts and stylesheets
        for src in self.response.css('script::attr(src), link[rel="stylesheet"]::attr(href)').getall():
            self._add_url(src)

        # Video/audio
        for src in self.response.css('video::attr(src), audio::attr(src)').getall():
            self._add_url(src)

        # Object/embed
        for src in self.response.css('object::attr(data), embed::attr(src)').getall():
            self._add_url(src)

        return iter(())

    def _extract_from_javascript(self) -> Iterator[str]:
        """Extract URLs from external JavaScript files"""
        script_urls = self.response.css('script::attr(src)').getall()
        for url in script_urls:
            self._add_url(url)

        return iter(())

    def _extract_from_inline_scripts(self) -> Iterator[str]:
        """Extract URLs from inline <script> tags"""
        scripts = self.response.css('script:not([src])::text').getall()

        for script in scripts:
            # Find all URL-like strings
            for match in self.URL_REGEX.finditer(script):
                self._add_url(match.group(0))

            # Find JavaScript variables containing URLs
            for pattern_str in self.JS_VAR_PATTERNS:
                pattern = re.compile(pattern_str, re.IGNORECASE)
                for match in pattern.finditer(script):
                    # Last group is usually the URL
                    url = match.groups()[-1]
                    self._add_url(url)

            # Find AJAX/fetch calls
            ajax_patterns = [
                r'\.fetch\(["\']([^"\']+)["\']\)',
                r'\.ajax\(\{[^}]*url:\s*["\']([^"\']+)["\']',
                r'XMLHttpRequest\.open\([^,]+,\s*["\']([^"\']+)["\']',
                r'axios\.(?:get|post|put|delete)\(["\']([^"\']+)["\']',
            ]

            for pattern_str in ajax_patterns:
                pattern = re.compile(pattern_str, re.IGNORECASE)
                for match in pattern.finditer(script):
                    self._add_url(match.group(1))

        return iter(())

    def _extract_from_event_handlers(self) -> Iterator[str]:
        """Extract URLs from onclick, onload, etc."""
        event_attrs = [
            'onclick', 'onload', 'onmouseover', 'onchange',
            'onsubmit', 'onerror', 'onfocus', 'onblur'
        ]

        for attr in event_attrs:
            handlers = self.response.xpath(f'//@{attr}').getall()
            for handler in handlers:
                # Look for window.location, document.location, etc.
                location_patterns = [
                    r'(?:window|document)\.location(?:\.href)?\s*=\s*["\']([^"\']+)["\']',
                    r'window\.open\(["\']([^"\']+)["\']',
                ]

                for pattern_str in location_patterns:
                    pattern = re.compile(pattern_str)
                    for match in pattern.finditer(handler):
                        self._add_url(match.group(1))

        return iter(())

    def _extract_from_css(self) -> Iterator[str]:
        """Extract URLs from CSS (inline styles)"""
        styles = self.response.css('[style]::attr(style)').getall()

        for style in styles:
            # Find url() declarations
            for match in re.finditer(r'url\(["\']?([^)"\']+)["\']?\)', style):
                self._add_url(match.group(1))

        return iter(())

    def _extract_from_style_tags(self) -> Iterator[str]:
        """Extract URLs from <style> tags"""
        style_contents = self.response.css('style::text').getall()

        for content in style_contents:
            # Find all url() declarations
            for match in re.finditer(r'url\(["\']?([^)"\']+)["\']?\)', content):
                self._add_url(match.group(1))

            # Find @import statements
            for match in re.finditer(r'@import\s+["\']([^"\']+)["\']', content):
                self._add_url(match.group(1))

        return iter(())

    def _extract_from_meta_tags(self) -> Iterator[str]:
        """Extract URLs from meta tags"""
        # Meta refresh
        meta_refresh = self.response.css('meta[http-equiv="refresh"]::attr(content)').getall()
        for content in meta_refresh:
            match = re.search(r'url=([^;]+)', content, re.IGNORECASE)
            if match:
                self._add_url(match.group(1))

        # Open Graph, Twitter Cards, etc.
        meta_url_attrs = [
            'og:url', 'og:image', 'twitter:url', 'twitter:image',
            'msapplication-TileImage', 'apple-touch-icon'
        ]

        for prop in meta_url_attrs:
            urls = self.response.css(f'meta[property="{prop}"]::attr(content), '
                                    f'meta[name="{prop}"]::attr(content)').getall()
            for url in urls:
                self._add_url(url)

        # Canonical URLs
        canonical = self.response.css('link[rel="canonical"]::attr(href)').get()
        if canonical:
            self._add_url(canonical)

        return iter(())

    def _extract_from_iframes(self) -> Iterator[str]:
        """Extract URLs from iframes and frame elements"""
        for src in self.response.css('iframe::attr(src), frame::attr(src)').getall():
            self._add_url(src)

        # Lazy-loaded iframes
        for data_src in self.response.css('iframe::attr(data-src), iframe::attr(data-lazy-src)').getall():
            self._add_url(data_src)

        return iter(())

    def _extract_from_data_uris(self) -> Iterator[str]:
        """Decode data URIs that might contain URLs"""
        data_uris = self.response.xpath('//@*[starts-with(., "data:")]').getall()

        for data_uri in data_uris:
            # Check if it's base64 encoded
            if ';base64,' in data_uri:
                try:
                    # Extract base64 part
                    _, encoded = data_uri.split(';base64,', 1)
                    decoded = base64.b64decode(encoded).decode('utf-8', errors='ignore')

                    # Look for URLs in decoded content
                    for match in self.URL_REGEX.finditer(decoded):
                        self._add_url(match.group(0))
                except Exception:
                    pass

        return iter(())

    def _extract_from_json_ld(self) -> Iterator[str]:
        """Extract URLs from JSON-LD structured data"""
        json_ld_scripts = self.response.css('script[type="application/ld+json"]::text').getall()

        for script in json_ld_scripts:
            try:
                data = json.loads(script)
                # Recursively find URLs in JSON structure
                urls = self._extract_urls_from_json(data)
                for url in urls:
                    self._add_url(url)
            except json.JSONDecodeError:
                pass

        return iter(())

    def _extract_urls_from_json(self, obj, depth=0) -> Iterator[str]:
        """Recursively extract URLs from JSON object"""
        if depth > 10:  # Prevent infinite recursion
            return

        if isinstance(obj, dict):
            for key, value in obj.items():
                # Check if key suggests URL
                if any(url_key in key.lower() for url_key in ['url', 'href', 'link', 'src', 'image']):
                    if isinstance(value, str):
                        yield value
                # Recurse into nested objects
                if isinstance(value, (dict, list)):
                    yield from self._extract_urls_from_json(value, depth + 1)

        elif isinstance(obj, list):
            for item in obj:
                yield from self._extract_urls_from_json(item, depth + 1)

        elif isinstance(obj, str):
            # Check if string looks like URL
            if obj.startswith(('http://', 'https://', '/', 'www.')):
                yield obj

        return iter(())

    def _extract_from_microdata(self) -> Iterator[str]:
        """Extract URLs from microdata (itemprop)"""
        urls = self.response.css('[itemprop*="url"]::attr(href), [itemprop*="image"]::attr(src)').getall()
        for url in urls:
            self._add_url(url)

        return iter(())

    def _extract_from_srcset(self) -> Iterator[str]:
        """Extract URLs from srcset attributes (responsive images)"""
        srcsets = self.response.css('img::attr(srcset), source::attr(srcset)').getall()

        for srcset in srcsets:
            # srcset format: "url 1x, url 2x" or "url 100w, url 200w"
            for part in srcset.split(','):
                url = part.strip().split()[0]  # Get URL part before size descriptor
                self._add_url(url)

        return iter(())

    def _extract_from_headers(self) -> Iterator[str]:
        """Extract URLs from HTTP Link headers"""
        link_headers = self.response.headers.getlist('Link')

        for header in link_headers:
            # Link header format: <url>; rel="..."
            matches = re.finditer(r'<([^>]+)>', header.decode('utf-8', errors='ignore'))
            for match in matches:
                self._add_url(match.group(1))

        return iter(())

    def _extract_from_encoded(self) -> Iterator[str]:
        """Extract URLs from encoded/obfuscated content"""
        html = self.response.text

        # Check for base64 encoded URLs
        for pattern in self.ENCODED_URL_PATTERNS:
            for match in pattern.finditer(html):
                encoded = match.group(1)
                try:
                    # Try to decode
                    if pattern.pattern.startswith('atob'):
                        # Base64
                        decoded = base64.b64decode(encoded).decode('utf-8', errors='ignore')
                    elif 'decodeURIComponent' in pattern.pattern:
                        # URL encoded
                        decoded = unquote(encoded)
                    else:
                        decoded = encoded

                    # Look for URLs in decoded content
                    for url_match in self.URL_REGEX.finditer(decoded):
                        self._add_url(url_match.group(0))
                except Exception:
                    pass

        return iter(())

    def _extract_from_query_params(self) -> Iterator[str]:
        """Extract URLs from query parameters (nested URLs)"""
        parsed = urlparse(self.base_url)
        params = parse_qs(parsed.query)

        for _key, values in params.items():
            for value in values:
                # Check if parameter value is a URL
                if value.startswith(('http://', 'https://', 'www.', '/')):
                    self._add_url(value)

                # Check for URL-encoded URLs
                try:
                    decoded = unquote(value)
                    if decoded.startswith(('http://', 'https://')):
                        self._add_url(decoded)
                except Exception:
                    pass

        return iter(())

    def _generate_from_patterns(self) -> Iterator[str]:
        """Generate potential URLs from discovered patterns"""
        # Find pagination patterns
        path = urlparse(self.base_url).path

        # Pattern: /page/1 → generate /page/2, /page/3, etc.
        page_match = re.search(r'/page/(\d+)', path)
        if page_match:
            current_page = int(page_match.group(1))
            for next_page in range(current_page + 1, min(current_page + 6, 100)):  # Next 5 pages
                new_url = self.base_url.replace(f'/page/{current_page}', f'/page/{next_page}')
                self._add_url(new_url)

        # Pattern: ?page=1 → generate ?page=2, ?page=3, etc.
        parsed = urlparse(self.base_url)
        params = parse_qs(parsed.query)
        if 'page' in params:
            try:
                current_page = int(params['page'][0])
                for next_page in range(current_page + 1, min(current_page + 6, 100)):
                    # Generate next page URL
                    new_params = params.copy()
                    new_params['page'] = [str(next_page)]
                    # Reconstruct URL
                    query = '&'.join(f"{k}={v[0]}" for k, v in new_params.items())
                    new_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{query}"
                    self._add_url(new_url)
            except (ValueError, IndexError):
                pass

        return iter(())

    def _add_url(self, url: str):
        """Normalize and add URL to discovered set"""
        if not url:
            return

        url = url.strip().strip('"\'')

        # Skip data URIs, javascript:, mailto:, tel:
        if url.startswith(('data:', 'javascript:', 'mailto:', 'tel:', '#')):
            return

        # Make absolute
        if not url.startswith(('http://', 'https://')):
            url = urljoin(self.base_url, url)

        # Remove fragment
        url = url.split('#')[0]

        # Skip if too long (likely malformed)
        if len(url) > 2000:
            return

        self.discovered_urls.add(url)


def extract_all_urls(response: Response) -> Iterator[str]:
    """Convenience function to extract ALL URLs from a response.

    Usage in discovery spider:
        from src.stage1.ultra_discovery import extract_all_urls

        def parse(self, response):
            for url in extract_all_urls(response):
                yield DiscoveryItem(discovered_url=url, ...)
    """
    discovery = UltraDiscovery(response)
    yield from discovery.discover_all()
