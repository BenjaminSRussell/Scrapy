"""RSS/Atom item-link ingest for the stage1 profile (#643).

The HTML scout and sitemap walk stay as they are. When ``stage1.feeds`` lists
feed URLs, the scout fetches those documents and enqueues each item link into
``seed_urls`` and ``stage2_queue``. An empty ``feeds`` list fetches nothing.

Parsing is offline and uses defusedxml. Feed bodies are untrusted XML.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring

from src.stage1.discovery_handoff import DiscoveryHandoff, ingest_handoff_via_seed_manager
from src.utils.ssrf import ssrf_block_reason
from src.utils.validation import url_rejection_reason

logger = logging.getLogger(__name__)

FEED_SOURCE = "feed_parser"
DEFAULT_MAX_ITEMS = 2000
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_REDIRECTS = {301, 302, 303, 307, 308}
_SKIP_ATOM_RELS = {"self", "enclosure", "replies", "via", "edit", "edit-media"}
_HEADERS = {
    "User-Agent": "FeedParser/1.0 (compatible; web crawler)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}


class FeedFetchError(RuntimeError):
    """A profile feed URL was refused or the document could not be downloaded."""


@dataclass(frozen=True)
class FeedParseResult:
    """Item links from one feed document."""

    urls: list[str]
    kind: str  # rss | atom | empty | unknown | error
    error: str | None = None


def profile_feed_urls(config: Any) -> list[str]:
    """``stage1.feeds`` (else ``stages.stage1.feeds``). Missing or invalid is ``[]``."""
    if config is None:
        return []
    for key in ("stage1.feeds", "stages.stage1.feeds"):
        try:
            value = config.get(key)
        except Exception:
            value = None
        if value is None:
            continue
        return _normalize_feed_list(value)
    return []


def parse_feed(
    content: bytes | str,
    *,
    feed_url: str = "",
    max_items: int = DEFAULT_MAX_ITEMS,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> FeedParseResult:
    """Extract RSS ``item`` and Atom ``entry`` links. No network."""
    if isinstance(content, str):
        raw = content.encode("utf-8")
    elif isinstance(content, (bytes, bytearray)):
        raw = bytes(content)
    else:
        return FeedParseResult(urls=[], kind="error", error="feed body is not bytes or text")
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    if len(raw) > max_bytes:
        return FeedParseResult(urls=[], kind="error", error=f"feed exceeds {max_bytes} bytes")
    if not raw.strip():
        return FeedParseResult(urls=[], kind="empty")
    try:
        root = safe_fromstring(raw)
    except (DefusedXmlException, ET.ParseError, ValueError) as exc:
        return FeedParseResult(urls=[], kind="error", error=str(exc))

    kind = _kind(root)
    limit = max(0, int(max_items))
    if kind == "rss":
        urls = _unique(_rss_links(root, feed_url), limit)
    elif kind == "atom":
        urls = _unique(_atom_links(root, feed_url), limit)
    else:
        return FeedParseResult(urls=[], kind="unknown", error="not an RSS or Atom feed")
    return FeedParseResult(urls=urls, kind=kind)


def fetch_feed(url: str, *, timeout: float = 15.0, max_bytes: int = DEFAULT_MAX_BYTES) -> bytes:
    """GET one profile feed. Refuses non-public URLs before any request."""
    current = url
    reason = ssrf_block_reason(current, resolve=False)
    if reason:
        raise FeedFetchError(f"refusing feed url ({reason})")
    with httpx.Client(timeout=timeout, follow_redirects=False, headers=_HEADERS) as client:
        for _hop in range(4):
            reason = ssrf_block_reason(current, resolve=False)
            if reason:
                raise FeedFetchError(f"refusing feed url ({reason})")
            with client.stream("GET", current) as response:
                if response.status_code in _REDIRECTS:
                    location = response.headers.get("location")
                    if not location:
                        raise FeedFetchError("redirect missing location")
                    current = urljoin(str(response.url), location)
                    continue
                if response.status_code >= 400:
                    raise FeedFetchError(f"feed fetch HTTP {response.status_code}")
                buf = bytearray()
                for chunk in response.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        raise FeedFetchError(f"feed exceeds {max_bytes} bytes")
                return bytes(buf)
    raise FeedFetchError("too many redirects")


def enqueue_feed_links(
    seed_manager: Any,
    urls: list[str],
    *,
    source_url: str,
    dedup: Any | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Merge item links into ``seed_urls`` and enqueue them on ``stage2_queue``.

    ``write_uconn_urls`` stays the ingest default (True). Domain-table writes
    still follow the profile's allowed domains.
    """
    site = urlparse(source_url).netloc or None
    handoff = DiscoveryHandoff.success(
        urls,
        discovery_source=FEED_SOURCE,
        job_id=job_id,
        site=site,
    )
    return ingest_handoff_via_seed_manager(
        seed_manager,
        handoff,
        source_url=source_url,
        dedup=dedup,
        ack_empty=True,
        enqueue_stage2=True,
    )


def _normalize_feed_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        logger.warning("Ignoring stage1.feeds; expected a list, got %s", type(value).__name__)
        return []
    urls: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            logger.warning("Ignoring non-string stage1.feeds entry %r", item)
            continue
        url = item.strip()
        if not url or url_rejection_reason(url):
            logger.warning("Ignoring feed URL %r", item)
            continue
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _local(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag


def _kind(root: ET.Element) -> str:
    name = _local(root.tag).lower()
    if name == "feed":
        return "atom"
    if name in {"rss", "rdf", "channel"}:
        return "rss"
    return "unknown"


def _child_text(parent: ET.Element, name: str) -> str | None:
    for child in list(parent):
        if _local(child.tag) != name:
            continue
        text = (child.text or "").strip()
        if text:
            return text
    return None


def _absolute(base: str, href: str) -> str | None:
    href = href.strip()
    if not href or href.startswith(("#", "javascript:", "mailto:", "data:")):
        return None
    absolute = urljoin(base, href) if base else href
    if url_rejection_reason(absolute):
        return None
    return absolute


def _rss_links(root: ET.Element, feed_url: str) -> list[str]:
    found: list[str] = []
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        link = _child_text(item, "link")
        if link:
            absolute = _absolute(feed_url, link)
            if absolute:
                found.append(absolute)
            continue
        for child in list(item):
            if _local(child.tag) != "guid":
                continue
            permalink = (child.get("isPermaLink") or "true").strip().lower()
            text = (child.text or "").strip()
            if permalink == "false" or not text:
                continue
            absolute = _absolute(feed_url, text)
            if absolute:
                found.append(absolute)
            break
    return found


def _atom_links(root: ET.Element, feed_url: str) -> list[str]:
    found: list[str] = []
    for entry in root.iter():
        if _local(entry.tag) != "entry":
            continue
        chosen: str | None = None
        for child in list(entry):
            if _local(child.tag) != "link":
                continue
            rel = (child.get("rel") or "alternate").strip().lower()
            href = (child.get("href") or "").strip() or (child.text or "").strip()
            if not href or rel in _SKIP_ATOM_RELS:
                continue
            if rel == "alternate":
                chosen = href
                break
            if chosen is None:
                chosen = href
        if chosen is None:
            entry_id = _child_text(entry, "id")
            if entry_id and entry_id.startswith(("http://", "https://")):
                chosen = entry_id
        if not chosen:
            continue
        absolute = _absolute(feed_url, chosen)
        if absolute:
            found.append(absolute)
    return found


def _unique(urls: list[str], limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= limit:
            break
    return out
