import base64
import binascii
import json
import logging
import re
from collections.abc import Iterable, Mapping
from re import Pattern
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from scrapy.http import Response

logger = logging.getLogger(__name__)

try:  # decode/parse failures that used to be swallowed by `except Exception: pass` (#385)
    from prometheus_client import Counter

    URL_EXTRACTOR_DECODE_FAILURES: Any = Counter(
        "scrapy_url_extractor_decode_failures_total",
        "URLExtractor inputs that could not be decoded/parsed, by source "
        "(atob, uri, json_ld, urljoin).",
        ["source"],
    )
except Exception:  # prometheus_client missing or metric already registered
    URL_EXTRACTOR_DECODE_FAILURES = None

try:  # which heuristic found each new URL, so noisy ones can be spotted and turned off (#27)
    from prometheus_client import Counter as _HCounter

    URL_EXTRACTOR_URLS: Any = _HCounter(
        "scrapy_url_extractor_urls_total",
        "URLs first found by each URLExtractor discovery heuristic.",
        ["heuristic"],
    )
except Exception:
    URL_EXTRACTOR_URLS = None

# Discovery heuristics, in the order they run. Each can be switched off in
# config.yml under ``stage1.discovery_heuristics`` (#27).
DISCOVERY_HEURISTICS: tuple[str, ...] = (
    "standard_tags",   # <a href>, <img src>, <link>, <iframe>, <form action>, media
    "inline_scripts",  # URLs, JS vars and atob/decodeURIComponent payloads in <script> text
    "script_tags",     # <script src>
    "css",             # url(...) in <style>
    "data_attributes", # data-href/data-url/... attributes
    "meta_tags",       # og:url, refresh, canonical-like meta
    "json_ld",         # application/ld+json blocks
    "comments",        # URLs inside HTML comments
    "event_handlers",  # onclick/onload/... attributes
    "raw_regex",       # regex over the whole body (noisiest; see #25)
)
# Off unless asked for: ``stage1.extract_hidden_urls: true`` or
# ``discovery_heuristics: {hidden_urls: true}`` (#481).
OPT_IN_HEURISTICS: tuple[str, ...] = (
    "hidden_urls",     # HiddenURLExtractor: meta refresh, /api/... literals, JS routes
)
ALL_HEURISTICS: tuple[str, ...] = DISCOVERY_HEURISTICS + OPT_IN_HEURISTICS
_FALSY = (False, "false", "off", "no", 0, "0")


def resolve_heuristics(setting: Any = None, config: Any = None) -> frozenset[str]:
    """Enabled heuristics from an explicit setting or ``stage1.discovery_heuristics``.

    Accepts a mapping ``{name: bool}`` (unlisted default heuristics stay
    enabled, opt-in ones stay off) or an iterable of enabled names. ``None``
    means "read config"; no config means every default heuristic.
    """
    hidden_flag = False
    if setting is None:
        if config is None:
            try:
                from src.core.config import get_config

                config = get_config()
            except Exception:
                config = None
        if config is not None:
            for key in ("stage1.discovery_heuristics", "stages.stage1.discovery_heuristics"):
                try:
                    setting = config.get(key)
                except Exception:
                    setting = None
                if setting is not None:
                    break
            for key in ("stage1.extract_hidden_urls", "stages.stage1.extract_hidden_urls"):
                try:
                    value = config.get(key)
                except Exception:
                    value = None
                if value is not None:
                    hidden_flag = value not in _FALSY and str(value).strip().lower() not in ("false", "off", "no", "0")
                    break
    extra = {"hidden_urls"} if hidden_flag else set()
    if setting is None:
        return frozenset(set(DISCOVERY_HEURISTICS) | extra)
    if isinstance(setting, Mapping):
        unknown = sorted(set(map(str, setting)) - set(ALL_HEURISTICS))
        enabled = {h for h in DISCOVERY_HEURISTICS if setting.get(h, True) not in _FALSY}
        for h in OPT_IN_HEURISTICS:
            if h in setting:
                if setting[h] not in _FALSY:
                    enabled.add(h)
            elif h in extra:
                enabled.add(h)
    elif isinstance(setting, Iterable) and not isinstance(setting, (str, bytes)):
        names = {str(h) for h in setting}
        unknown = sorted(names - set(ALL_HEURISTICS))
        enabled = (names & set(ALL_HEURISTICS)) | extra
    else:
        logger.warning(f"[URLExtractor] Ignoring discovery_heuristics={setting!r}; expected a mapping or list")
        return frozenset(set(DISCOVERY_HEURISTICS) | extra)
    if unknown:
        logger.warning(f"[URLExtractor] Unknown discovery heuristics ignored: {unknown}; known: {list(ALL_HEURISTICS)}")
    return frozenset(enabled)

_PAYLOAD_PREVIEW = 80


def _record_decode_failure(source: str, payload: str, error: Exception) -> None:
    if URL_EXTRACTOR_DECODE_FAILURES is not None:
        URL_EXTRACTOR_DECODE_FAILURES.labels(source=source).inc()
    preview = payload if len(payload) <= _PAYLOAD_PREVIEW else payload[:_PAYLOAD_PREVIEW] + "..."
    logger.debug(f"[URLExtractor] {source} decode failed ({type(error).__name__}: {error}): {preview!r}")

class URLExtractor:

    URL_REGEX = re.compile(
        r"(?<![{\[<$%#])(?:(?:https?|ftp):)?//[\w\-\.]+(?::\d+)?(?:/[\w\-\./?%&=]*)?"
        r"|(?<![{\[<$%#])(?:www\.)?[\w\-]+\.(?:edu|com|org|net|gov|io|co)(?:/[\w\-\./?%&=]*)?",
        re.IGNORECASE,
    )

    ENCODED_URL_PATTERNS: list[Pattern[str]] = [
        re.compile(r'atob\(["\']([^"\']+)["\']\)'),
        re.compile(r'decodeURIComponent\(["\']([^"\']+)["\']\)'),
        re.compile(r'unescape\(["\']([^"\']+)["\']\)'),
    ]
    # Which decoder each ENCODED_URL_PATTERNS entry needs (#385).
    ENCODED_URL_DECODERS: tuple[str, ...] = ("atob", "uri", "unescape")

    JS_VAR_PATTERNS = [
        r'(?:var|let|const)\s+(\w+)\s*=\s*["\']([^"\']*(?:https?://|/)[^"\']+)["\']',
        r'(\w+)\s*:\s*["\']([^"\']*(?:https?://|/)[^"\']+)["\']',
        r'(?:url|href|src|endpoint|api|link)\s*[:=]\s*["\']([^"\']+)["\']',
        r'(?:fetch|axios\.get|axios\.post|\.get|\.post)\s*\(\s*["\']([^"\']+)["\']',
    ]

    def __init__(self, base_url: str, allowed_domains: list[str], heuristics: Any = None):
        """``heuristics``: mapping or list of enabled heuristics; None reads config (#27)."""
        self.base_url = base_url
        self.allowed_domains = allowed_domains
        self.discovered_urls: set[str] = set()
        self.heuristics = resolve_heuristics(heuristics)
        self.heuristic_counts: dict[str, int] = {}

    def discover_all_urls(self, response: Response) -> set[str]:
        self.discovered_urls = set()
        self.heuristic_counts = {}

        for name in ALL_HEURISTICS:
            if name not in self.heuristics:
                continue
            before = len(self.discovered_urls)
            getattr(self, f"_extract_from_{name}")(response)
            found = len(self.discovered_urls) - before
            self.heuristic_counts[name] = found
            if found and URL_EXTRACTOR_URLS is not None:
                URL_EXTRACTOR_URLS.labels(heuristic=name).inc(found)

        return self.discovered_urls

    def extract_sitemap_urls(self, response: Response) -> set[str]:
        return set()

    # HiddenURLExtractor categories worth crawling. "sitemaps" is left out: it
    # guesses /sitemap.xml variants for every page; sitemap_parser owns that.
    HIDDEN_URL_CATEGORIES: tuple[str, ...] = (
        "data_attributes",
        "json_ld",
        "javascript",
        "iframes",
        "meta_refresh",
        "api_endpoints",
    )

    def _extract_from_hidden_urls(self, response: Response):
        from src.stage1.processors.hidden_url_extractor import HiddenURLExtractor

        found = HiddenURLExtractor(base_url=self.base_url).extract_all_hidden_urls(response)
        for category in self.HIDDEN_URL_CATEGORIES:
            for url in found.get(category, ()):
                self._add_url(url)

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

            for encoded_pattern, decoder in zip(self.ENCODED_URL_PATTERNS, self.ENCODED_URL_DECODERS):
                for match in encoded_pattern.finditer(script):
                    decoded = self._decode_url(match.group(1), decoder)
                    if decoded:
                        self._add_url(decoded)

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
        for script in response.css('script[type="application/ld+json"]::text').getall():
            try:
                data = json.loads(script)
            except (ValueError, RecursionError) as e:  # JSONDecodeError is a ValueError
                _record_decode_failure("json_ld", script.strip(), e)
                continue
            self._extract_urls_from_json(data)

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
            "##",
        ]
        return any(indicator in url for indicator in template_indicators)

    @staticmethod
    def _looks_like_url(decoded: str) -> bool:
        return "http" in decoded or decoded.startswith("/")

    def _decode_url(self, encoded: str, decoder: str = "auto") -> str | None:
        """Decode an atob()/decodeURIComponent()/unescape() argument (#385).

        ``decoder`` is ``atob`` (base64 + UTF-8, like JS: whitespace ignored,
        padding optional), ``uri`` (decodeURIComponent: percent-decoding as
        UTF-8), ``unescape`` (percent-decoding as Latin-1, never fails) or
        ``auto`` (base64, then percent-decoding). Malformed input is counted in
        ``scrapy_url_extractor_decode_failures_total{source}`` and logged at DEBUG
        with a truncated payload; nothing is swallowed silently.
        """
        if decoder in ("atob", "auto"):
            try:
                compact = re.sub(r"\s+", "", encoded)
                compact += "=" * (-len(compact) % 4)
                decoded = base64.b64decode(compact, validate=True).decode("utf-8")
            except (binascii.Error, ValueError) as e:  # bad alphabet/padding; UnicodeDecodeError
                if decoder == "atob":
                    _record_decode_failure("atob", encoded, e)
                    return None
            else:
                if self._looks_like_url(decoded):
                    return decoded
                if decoder == "atob":
                    return None
        if decoder == "unescape":
            decoded = unquote(encoded, encoding="latin-1")
            return decoded if self._looks_like_url(decoded) else None
        try:
            decoded = unquote(encoded, errors="strict")
        except UnicodeDecodeError as e:  # e.g. %FF%FE: not valid UTF-8 once unquoted
            _record_decode_failure("uri", encoded, e)
            return None
        return decoded if self._looks_like_url(decoded) else None

    def _add_url(self, url: str):
        if not url or not isinstance(url, str):
            return

        url = url.strip()

        if (
            not url
            or url == "#"
            or url.startswith("javascript:")
            or url.startswith("mailto:")
            or url.startswith("tel:")
        ):
            return

        if url.startswith("data:"):
            return

        try:
            absolute_url = urljoin(self.base_url, url)
        except ValueError as e:  # e.g. invalid IPv6 netloc "http://[::1"
            _record_decode_failure("urljoin", url, e)
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

        except ValueError:  # urlparse rejects malformed netlocs (invalid IPv6, bad port)
            return False
