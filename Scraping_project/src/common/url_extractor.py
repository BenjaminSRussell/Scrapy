import base64
import logging
import re
from re import Pattern
from urllib.parse import unquote, urljoin, urlparse

from scrapy.http import Response

logger = logging.getLogger(__name__)

class URLExtractor:

    URL_REGEX = re.compile(
        r"(?<![{\[<$%
        r"|(?<![{\[<$%
        re.IGNORECASE,
    )

    ENCODED_URL_PATTERNS: list[Pattern[str]] = [
        re.compile(r'atob\(["\']([^"\']+)["\']\)'),
        re.compile(r'decodeURIComponent\(["\']([^"\']+)["\']\)'),
        re.compile(r'unescape\(["\']([^"\']+)["\']\)'),
    ]

    JS_VAR_PATTERNS = [
        r'(?:var|let|const)\s+(\w+)\s*=\s*["\']([^"\']*(?:https?://|/)[^"\']+)["\']',
        r'(\w+)\s*:\s*["\']([^"\']*(?:https?://|/)[^"\']+)["\']',
        r'(?:url|href|src|endpoint|api|link)\s*[:=]\s*["\']([^"\']+)["\']',
        r'(?:fetch|axios\.get|axios\.post|\.get|\.post)\s*\(\s*["\']([^"\']+)["\']',
    ]

    def __init__(self, base_url: str, allowed_domains: list[str]):
        self.base_url = base_url
        self.allowed_domains = allowed_domains
        self.discovered_urls: set[str] = set()

    def discover_all_urls(self, response: Response) -> set[str]:
        self.discovered_urls = set()

        self._extract_from_standard_tags(response)

        self._extract_from_inline_scripts(response)

        self._extract_from_script_tags(response)

        self._extract_from_css(response)

        self._extract_from_data_attributes(response)

        self._extract_from_meta_tags(response)

        self._extract_from_json_ld(response)

        self._extract_from_comments(response)

        self._extract_from_event_handlers(response)

        self._extract_from_raw_regex(response)

        return self.discovered_urls

    def extract_sitemap_urls(self, response: Response) -> set[str]:
        return set()

    def _extract_from_standard_tags(self, response: Response):
        for href in response.css("a::attr(href)").getall():
            self._add_url(href)

        for src in response.css("img::attr(src)").getall():
            self._add_url(src)

        for href in response.css("link::attr(href)").getall():
            self._add_url(href)

        for src in response.css("iframe::attr(src)").getall():
            self._add_url(src)

        for action in response.css("form::attr(action)").getall():
            self._add_url(action)

        for src in response.css("embed::attr(src), object::attr(data)").getall():
            self._add_url(src)

        for src in response.css("source::attr(src)").getall():
            self._add_url(src)

        for src in response.css("video::attr(src), audio::attr(src)").getall():
            self._add_url(src)

    def _extract_from_inline_scripts(self, response: Response):
        for script in response.css("script::text").getall():
            for match in self.URL_REGEX.finditer(script):
                self._add_url(match.group())

            for pattern_str in self.JS_VAR_PATTERNS:
                for match in re.finditer(pattern_str, script):
                    url = match.groups()[-1]
                    self._add_url(url)

            for encoded_pattern in self.ENCODED_URL_PATTERNS:
                for match in encoded_pattern.finditer(script):
                    encoded = match.group(1)
                    try:
                        decoded = self._decode_url(encoded)
                        if decoded:
                            self._add_url(decoded)
                    except Exception:
                        pass

    def _extract_from_script_tags(self, response: Response):
        for src in response.css("script::attr(src)").getall():
            self._add_url(src)

    def _extract_from_css(self, response: Response):
        for style in response.css("style::text").getall():
            url_pattern = re.compile(r'url\(["\']?([^"\']+)["\']?\)', re.IGNORECASE)
            for match in url_pattern.finditer(style):
                self._add_url(match.group(1))

            import_pattern = re.compile(r'@import\s+["\']([^"\']+)["\']', re.IGNORECASE)
            for match in import_pattern.finditer(style):
                self._add_url(match.group(1))

        for style in response.css("[style]::attr(style)").getall():
            url_pattern = re.compile(r'url\(["\']?([^"\']+)["\']?\)', re.IGNORECASE)
            for match in url_pattern.finditer(style):
                self._add_url(match.group(1))

    def _extract_from_data_attributes(self, response: Response):
        data_attrs = [
            "data-src",
            "data-href",
            "data-url",
            "data-link",
            "data-image",
            "data-background",
            "data-lazy-src",
            "data-original",
            "data-lazy",
            "data-bg",
        ]

        for attr in data_attrs:
            for url in response.css(f"[{attr}]::attr({attr})").getall():
                self._add_url(url)

    def _extract_from_meta_tags(self, response: Response):
        for url in response.css('link[rel="canonical"]::attr(href)').getall():
            self._add_url(url)

        for url in response.css('link[rel="alternate"]::attr(href)').getall():
            self._add_url(url)

        for url in response.css('meta[property="og:url"]::attr(content)').getall():
            self._add_url(url)

        for url in response.css('meta[property="og:image"]::attr(content)').getall():
            self._add_url(url)

        for url in response.css('meta[name="twitter:image"]::attr(content)').getall():
            self._add_url(url)

    def _extract_from_json_ld(self, response: Response):
        import json

        for script in response.css('script[type="application/ld+json"]::text').getall():
            try:
                data = json.loads(script)
                self._extract_urls_from_json(data)
            except Exception:
                pass

    def _extract_urls_from_json(self, data):
        if isinstance(data, dict):
            for _key, value in data.items():
                if isinstance(value, str) and ("http://" in value or "https://" in value or value.startswith("/")):
                    self._add_url(value)
                else:
                    self._extract_urls_from_json(value)
        elif isinstance(data, list):
            for item in data:
                self._extract_urls_from_json(item)

    def _extract_from_comments(self, response: Response):
        comment_pattern = re.compile(r"<!--(.*?)-->", re.DOTALL)
        for match in comment_pattern.finditer(response.text):
            comment = match.group(1)
            for url_match in self.URL_REGEX.finditer(comment):
                self._add_url(url_match.group())

    def _extract_from_event_handlers(self, response: Response):
        event_attrs = ["onclick", "onload", "onerror", "onmouseover", "onfocus"]

        for attr in event_attrs:
            for handler in response.css(f"[{attr}]::attr({attr})").getall():
                for match in self.URL_REGEX.finditer(handler):
                    self._add_url(match.group())

    def _extract_from_raw_regex(self, response: Response):
        for match in self.URL_REGEX.finditer(response.text):
            url = match.group()
            if not self._is_likely_template(url):
                self._add_url(url)

    def _is_likely_template(self, url: str) -> bool:
        template_indicators = [
            "{",
            "}",
            "{{",
            "}}",
            "${",
            "}",
            "<%",
            "%>",
            "[%",
            "%]",
            "__",
            "
        ]
        return any(indicator in url for indicator in template_indicators)

    def _decode_url(self, encoded: str) -> str | None:
        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
            if "http" in decoded or decoded.startswith("/"):
                return decoded
        except Exception:
            pass

        try:
            decoded = unquote(encoded)
            if "http" in decoded or decoded.startswith("/"):
                return decoded
        except Exception:
            pass

        return None

    def _add_url(self, url: str):
        if not url or not isinstance(url, str):
            return

        url = url.strip()

        if (
            not url
            or url == "
            or url.startswith("javascript:")
            or url.startswith("mailto:")
            or url.startswith("tel:")
        ):
            return

        if url.startswith("data:"):
            return

        try:
            absolute_url = urljoin(self.base_url, url)
        except Exception:
            return

        if not self._is_valid_url(absolute_url):
            return

        self.discovered_urls.add(absolute_url)

    def _is_valid_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)

            if not parsed.scheme or not parsed.netloc:
                return False

            if parsed.scheme not in ["http", "https"]:
                return False

            if self.allowed_domains:
                domain_matched = any(
                    parsed.netloc == domain or parsed.netloc.endswith("." + domain) for domain in self.allowed_domains
                )
                if not domain_matched:
                    return False

            placeholder_domains = [
                "example.com",
                "example.org",
                "localhost",
                "127.0.0.1",
                "test.com",
                "domain.com",
            ]
            if not self.allowed_domains:
                if any(placeholder in parsed.netloc for placeholder in placeholder_domains):
                    return False

            return True

        except Exception:
            return False
