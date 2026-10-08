"""One URL canonicalization for Redis, Kafka and Delta (#728).

Before this module there were three normalizers that disagreed (URLProcessor,
``validation.normalize_url``, and raw strings in Redis/SeedManager), so a URL
deduplicated in Redis could still be written to the lake several times as
``/page`` vs ``/page/`` vs ``?utm_source=x`` variants with different
``url_hash`` values.

Rules (identical to the URLProcessor rules Stage 1 already hashed with, so
existing scout ``url_hash`` values are unchanged):

* only http/https with a host; anything else -> ``None``
* lowercase scheme, host and path; drop default ports (:80 / :443)
* drop the fragment; strip a trailing slash (except the root ``/``)
* drop tracking parameters (``utm_*``, fbclid, gclid, ...) and sort the rest
* internationalized hosts (#207): NFKC, then IDNA punycode (``bücher.de`` and
  ``xn--bcher-kva.de`` are the same URL); a trailing root dot is dropped;
  undecodable hosts -> ``None``
* paths (#207): NFKC, non-ASCII characters percent-encoded as UTF-8, escapes of
  unreserved ASCII characters decoded (``%7E`` -> ``~``). Other escapes are
  kept as-is, so overlong/invalid UTF-8 sequences such as ``%C0%AF`` are never
  decoded into ``/`` (no path-traversal smuggling).

ASCII-only URLs without unreserved-character escapes keep the exact canonical
form (and ``url_hash``) they had before #207.

``canonicalize_url`` is idempotent: ``c(c(u)) == c(u)``.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)

TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "ref", "source", "campaign", "_ga", "_gid", "_gl",
})


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k in TRACKING_PARAMS or k.startswith("utm_")


def _canonical_query(query: str) -> str:
    try:
        params = parse_qs(query, keep_blank_values=True)
    except ValueError:
        return query
    kept = sorted((k, v) for k, v in params.items() if not _is_tracking(k))
    return urlencode(kept, doseq=True) if kept else ""


_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_PCT = re.compile(r"%([0-9A-Fa-f]{2})")
# Characters left literal when percent-encoding a path: RFC 3986 pchar + "/" + "%".
_PATH_SAFE = "/:@!$&'()*+,;=-._~%"


def _canonical_host(host: str) -> str | None:
    """Lowercase, NFKC + IDNA (punycode) host; None if it cannot be encoded (#207)."""
    host = host.rstrip(".") if host not in (".", "") else host
    if host.isascii():
        host = host.lower()
        if "xn--" not in host:
            return host
    try:
        labels = unicodedata.normalize("NFKC", host).lower().replace("\u3002", ".").split(".")
        return ".".join(lab.encode("idna").decode("ascii") if lab else lab for lab in labels)
    except (UnicodeError, ValueError):
        return None


def _canonical_netloc(netloc: str) -> str | None:
    userinfo, at, hostport = netloc.rpartition("@")
    if hostport.startswith("["):  # IPv6 literal
        return (userinfo + at + hostport).lower()
    host, colon, port = hostport.rpartition(":") if ":" in hostport else (hostport, "", "")
    if colon and not port.isdigit():
        host, colon, port = hostport, "", ""
    canon = _canonical_host(host)
    if canon is None or not canon:
        return None
    return (userinfo + at).lower() + canon + colon + port


def _canonical_path(path: str) -> str:
    """NFKC + UTF-8 percent-encoding for non-ASCII; decode only unreserved escapes (#207)."""
    if not path.isascii():
        path = quote(unicodedata.normalize("NFKC", path), safe=_PATH_SAFE)
    if "%" in path:
        def _fix(m: re.Match[str]) -> str:
            ch = chr(int(m.group(1), 16))
            return ch if ch in _UNRESERVED else m.group(0)

        path = _PCT.sub(_fix, path)
    return path


def canonicalize_url(url: str) -> str | None:
    """Canonical form of ``url``, or None if it is not an http(s) URL with a host."""
    if not isinstance(url, str):
        return None
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https") or not parsed.netloc:
        return None
    netloc = _canonical_netloc(parsed.netloc)
    if netloc is None:
        return None
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    path = _canonical_path(parsed.path or "/").lower()
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    query = _canonical_query(parsed.query) if parsed.query else ""
    return urlunparse((scheme, netloc, path, "", query, ""))


def canonical_or_raw(url: str) -> str:
    """Canonical URL, falling back to the input for non-http(s) strings."""
    return canonicalize_url(url) or (url if isinstance(url, str) else str(url))


def url_hash(url: str) -> str:
    """The pipeline-wide ``url_hash``: sha256 of the canonical URL, first 16 hex chars."""
    return hashlib.sha256(canonical_or_raw(url).encode("utf-8")).hexdigest()[:16]
