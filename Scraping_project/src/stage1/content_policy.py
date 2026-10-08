"""Which responses may enter HTML parsing (#662).

One policy for every Stage 1 spider, so binary or non-HTML bodies never reach
CSS/XPath extraction:

- empty body                         -> skip ``empty_body``
- Content-Type text/html or XHTML    -> parse, unless the body is binary
  (PDF/ZIP/PNG/JPEG/GIF/gzip magic or NUL bytes) -> skip ``binary_body_mislabeled``
- Content-Type missing/blank         -> sniff: binary -> skip ``binary_body``;
  an HTML document start -> parse (``sniffed_html``); otherwise skip
  ``missing_content_type``
- any other Content-Type             -> skip ``non_html:<media type>``
- a parse decision on a non-text Scrapy Response -> skip ``not_text_response``

Header values are normalised (case, parameters, whitespace, undecodable
bytes), so malformed headers cannot bypass the policy.

Stage 2 (``Stage2Worker._fetch_once``) applies the same matrix (#205), plus
routing for documents:

- ``application/pdf``, or a ``%PDF-`` body under any/no header -> ``stage4_large_docs``
- a parse decision above                                    -> HTML analysis
- everything else (JSON, images, Office, mislabeled binary)  -> minimal record,
  never the HTML analyzer
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})
BINARY_MAGIC = (
    b"%PDF-",
    b"PK\x03\x04",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"\x1f\x8b",
    b"Rar!",
    b"7z\xbc\xaf",
    b"\x00\x00\x01\x00",  # ico
    b"OggS",
    b"ID3",
)
_HTML_STARTS = (b"<!doctype html", b"<html", b"<head", b"<body", b"<?xml")
SNIFF_BYTES = 1024


@dataclass(frozen=True)
class ContentDecision:
    parse_html: bool
    reason: str
    media_type: str


def media_type(raw: Any) -> str:
    """Normalised media type from a Content-Type header value (bytes/str/None)."""
    if raw is None:
        return ""
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("latin-1", errors="ignore")
    value = str(raw).split(";", 1)[0].split(",", 1)[0]
    return "".join(value.split()).lower()


def looks_binary(body: bytes) -> bool:
    head = body[:SNIFF_BYTES]
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if any(stripped.startswith(m) for m in BINARY_MAGIC):
        return True
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False  # UTF-16 BOM: text with NULs by design
    return b"\x00" in head


def looks_like_html(body: bytes) -> bool:
    head = body[:SNIFF_BYTES].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return head.startswith(_HTML_STARTS) or b"<html" in head


def classify(content_type: Any, body: bytes, *, is_text_response: bool = True) -> ContentDecision:
    mt = media_type(content_type)
    body = body or b""
    if not body.strip():
        return ContentDecision(False, "empty_body", mt)
    if mt in HTML_MEDIA_TYPES:
        if looks_binary(body):
            return ContentDecision(False, "binary_body_mislabeled", mt)
        decision = ContentDecision(True, "html", mt)
    elif not mt:
        if looks_binary(body):
            return ContentDecision(False, "binary_body", mt)
        if not looks_like_html(body):
            return ContentDecision(False, "missing_content_type", mt)
        decision = ContentDecision(True, "sniffed_html", mt)
    else:
        return ContentDecision(False, f"non_html:{mt}", mt)
    if not is_text_response:
        return ContentDecision(False, "not_text_response", mt)
    return decision


def classify_response(response: Any) -> ContentDecision:
    from scrapy.http import TextResponse

    return classify(
        response.headers.get("Content-Type"),
        response.body,
        is_text_response=isinstance(response, TextResponse),
    )


try:
    from prometheus_client import Counter as _Counter

    NON_HTML_SKIPPED = _Counter(
        "scrapy_non_html_skipped_total",
        "Responses kept out of HTML parsing by the content policy, by spider and reason.",
        ["spider", "reason"],
    )
except Exception:  # prometheus_client missing or metric already registered
    NON_HTML_SKIPPED = None


def count_skipped(spider: str, reason: str) -> None:
    if NON_HTML_SKIPPED is not None:
        NON_HTML_SKIPPED.labels(spider=spider, reason=reason.split(":", 1)[0]).inc()
