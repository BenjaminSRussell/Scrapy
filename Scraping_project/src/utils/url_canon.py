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

``canonicalize_url`` is idempotent: ``c(c(u)) == c(u)``.
"""

from __future__ import annotations

import hashlib
import logging
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

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
    netloc = parsed.netloc.lower()
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    path = (parsed.path or "/").lower()
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
