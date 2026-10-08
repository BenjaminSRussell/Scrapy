"""Per-request fetch policy: large-document timeouts (#396), cookie scope (#395).

Large documents
    ``DOWNLOAD_TIMEOUT`` stays short for HTML (config ``download_timeout``), but
    a PDF/Office/archive URL that Stage 4 will later summarize gets
    ``LARGE_DOC_DOWNLOAD_TIMEOUT`` and ``LARGE_DOC_MAXSIZE`` instead. Detection
    is by URL path extension (``LARGE_DOC_EXTENSIONS``) or a regex in
    ``LARGE_DOC_URL_PATTERNS`` (for ``/download?id=`` style links), or a
    request carrying ``meta["large_document"] = True``. The timeout must be
    chosen before the response (and its Content-Type) exists, so this is
    URL-based by design. An explicit ``download_timeout``/``download_maxsize``
    in the request meta always wins. Runs before Scrapy's
    DownloadTimeoutMiddleware (350), which only ``setdefault``s the meta key.

Cookies
    ``COOKIES_ENABLED`` is config-driven and defaults to False. When it is on
    and ``COOKIES_ALLOWED_DOMAINS`` is non-empty, only those hosts (and their
    subdomains) get a cookie jar; every other request is marked
    ``dont_merge_cookies`` so CookiesMiddleware (700) neither sends nor stores
    cookies for it. An empty list means "all hosts" (plain Scrapy behaviour).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_LARGE_DOC_EXTENSIONS: tuple[str, ...] = (
    "pdf",
    "doc",
    "docx",
    "ppt",
    "pptx",
    "xls",
    "xlsx",
    "odt",
    "odp",
    "ods",
    "rtf",
    "epub",
    "zip",
)
DEFAULT_LARGE_DOC_TIMEOUT = 120.0
DEFAULT_LARGE_DOC_MAXSIZE = 100 * 1024 * 1024


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def url_extension(url: str) -> str:
    """Lower-cased extension of the URL *path* (query and fragment ignored)."""
    path = urlparse(url).path
    last = path.rsplit("/", 1)[-1]
    if "." not in last:
        return ""
    return last.rsplit(".", 1)[-1].lower()


def host_allowed(host: str, allowed: Iterable[str]) -> bool:
    """True if ``host`` equals or is a subdomain of an entry in ``allowed``."""
    host = (host or "").lower().rstrip(".")
    for domain in allowed:
        domain = domain.lower().lstrip(".").rstrip(".")
        if domain and (host == domain or host.endswith("." + domain)):
            return True
    return False


class FetchPolicyMiddleware:
    def __init__(
        self,
        *,
        base_timeout: float = 10.0,
        base_maxsize: int = 0,
        large_doc_timeout: float = DEFAULT_LARGE_DOC_TIMEOUT,
        large_doc_maxsize: int = DEFAULT_LARGE_DOC_MAXSIZE,
        large_doc_extensions: Iterable[str] = DEFAULT_LARGE_DOC_EXTENSIONS,
        large_doc_url_patterns: Iterable[str] = (),
        cookies_enabled: bool = False,
        cookies_allowed_domains: Iterable[str] = (),
        stats: Any = None,
    ):
        self.base_timeout = float(base_timeout)
        # Never *shorten* a request below the global timeout.
        self.large_doc_timeout = max(float(large_doc_timeout), self.base_timeout)
        # 0 = unlimited. A large-doc cap never undercuts the global DOWNLOAD_MAXSIZE,
        # and an unlimited global cap is left unlimited.
        base_maxsize = int(base_maxsize)
        large_doc_maxsize = int(large_doc_maxsize)
        if base_maxsize <= 0 or large_doc_maxsize <= 0:
            self.large_doc_maxsize = 0
        else:
            self.large_doc_maxsize = max(large_doc_maxsize, base_maxsize)
        self.large_doc_extensions = frozenset(e.lower().lstrip(".") for e in large_doc_extensions)
        self.large_doc_patterns = [re.compile(p) for p in large_doc_url_patterns]
        self.cookies_enabled = bool(cookies_enabled)
        self.cookies_allowed_domains = [d for d in cookies_allowed_domains if d]
        self.stats = stats

    @classmethod
    def from_crawler(cls, crawler: Any) -> "FetchPolicyMiddleware":
        s = crawler.settings
        exts = s.get("LARGE_DOC_EXTENSIONS")
        return cls(
            base_timeout=s.getfloat("DOWNLOAD_TIMEOUT", 180.0),
            base_maxsize=s.getint("DOWNLOAD_MAXSIZE", 1024 * 1024 * 1024),
            large_doc_timeout=s.getfloat("LARGE_DOC_DOWNLOAD_TIMEOUT", DEFAULT_LARGE_DOC_TIMEOUT),
            large_doc_maxsize=s.getint("LARGE_DOC_MAXSIZE", DEFAULT_LARGE_DOC_MAXSIZE),
            large_doc_extensions=_as_list(exts) if exts is not None else DEFAULT_LARGE_DOC_EXTENSIONS,
            large_doc_url_patterns=_as_list(s.get("LARGE_DOC_URL_PATTERNS")),
            cookies_enabled=as_bool(s.get("COOKIES_ENABLED"), default=False),
            cookies_allowed_domains=_as_list(s.get("COOKIES_ALLOWED_DOMAINS")),
            stats=getattr(crawler, "stats", None),
        )

    def is_large_document(self, request: Any) -> bool:
        if request.meta.get("large_document"):
            return True
        if url_extension(request.url) in self.large_doc_extensions:
            return True
        return any(p.search(request.url) for p in self.large_doc_patterns)

    def process_request(self, request: Any, spider: Any = None) -> None:
        if self.is_large_document(request):
            request.meta.setdefault("download_timeout", self.large_doc_timeout)
            if self.large_doc_maxsize > 0:
                request.meta.setdefault("download_maxsize", self.large_doc_maxsize)
            self._inc("fetch_policy/large_document")

        if self.cookies_enabled and self.cookies_allowed_domains:
            host = urlparse(request.url).hostname or ""
            if not host_allowed(host, self.cookies_allowed_domains):
                request.meta.setdefault("dont_merge_cookies", True)
                self._inc("fetch_policy/cookieless")
        return None

    def _inc(self, key: str) -> None:
        if self.stats is not None:
            self.stats.inc_value(key)
