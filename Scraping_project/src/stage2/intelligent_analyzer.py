import json
import logging
import posixpath
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from src.core.config import stage2_quality_thresholds
from src.utils.delta import get_delta

logger = logging.getLogger(__name__)

# Stub cap so one calendar page cannot blow up the analysis JSON (#648).
_EVENT_STUB_CAP = 50
_EVENT_STUB_KEYS = ("source", "name", "start", "end", "url", "location")
EVENT_EXTRACT_FLAG = "STAGE2_EVENT_EXTRACT_ENABLED"


def _is_pdf_link(href: str) -> bool:
    """``.pdf`` by path extension, so ``Report.PDF`` and ``a.pdf?dl=1#p2`` count (#221)."""
    try:
        path = urlparse(href).path
    except ValueError:
        return False
    return posixpath.splitext(path)[1].lower() == ".pdf"


def _event_extract_enabled() -> bool:
    """Experimental. Off unless STAGE2_EVENT_EXTRACT_ENABLED is set (#648)."""
    from src.utils.feature_flags import get_bool

    return get_bool(EVENT_EXTRACT_FLAG, False)


def events_json_field(html: str) -> str | None:
    """JSON list of event stubs, or None when the flag is off.

    The string is the analysis ``events`` field. Absent microdata is ``[]``.
    """
    if not _event_extract_enabled():
        return None
    return json.dumps(extract_event_stubs(html), separators=(",", ":"), ensure_ascii=False)


def extract_event_stubs(html: str) -> list[dict[str, str]]:
    """schema.org Event (JSON-LD and microdata) and h-event stubs. Never raises."""
    if not isinstance(html, str) or not html:
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
        rows = _jsonld_event_stubs(soup)
        rows.extend(_microdata_event_stubs(soup))
        rows.extend(_h_event_stubs(soup))
        return _cap_event_stubs(rows)
    except Exception as exc:
        logger.warning("event microformat extraction failed: %s", exc)
        return []


def _local_schema_name(token: str) -> str:
    return token.rsplit("/", 1)[-1].rsplit(":", 1)[-1].strip()


def _schema_type_names(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = [value]
    elif isinstance(value, list):
        raw = [item for item in value if isinstance(item, str)]
    else:
        return []
    return [_local_schema_name(item) for item in raw if item.strip()]


def _is_schema_event(value: Any) -> bool:
    return any(name == "Event" or name.endswith("Event") for name in _schema_type_names(value))


def _text_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("name", "url", "@id"):
            text = _text_value(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, list):
        parts = [_text_value(item) for item in value]
        return ", ".join(part for part in parts if part)
    return ""


def _schema_stub(node: dict[str, Any]) -> dict[str, str]:
    url = _text_value(node.get("url")) or _text_value(node.get("@id"))
    return {
        "source": "schema.org",
        "name": _text_value(node.get("name")),
        "start": _text_value(node.get("startDate")),
        "end": _text_value(node.get("endDate")),
        "url": url,
        "location": _text_value(node.get("location")),
    }


def _walk_jsonld(node: Any, rows: list[dict[str, str]], depth: int) -> None:
    if depth > 12 or len(rows) >= _EVENT_STUB_CAP:
        return
    if isinstance(node, list):
        for item in node:
            _walk_jsonld(item, rows, depth + 1)
        return
    if not isinstance(node, dict):
        return
    if _is_schema_event(node.get("@type")):
        rows.append(_schema_stub(node))
    for key, value in node.items():
        if key == "@type" or not isinstance(value, (dict, list)):
            continue
        _walk_jsonld(value, rows, depth + 1)


def _jsonld_event_stubs(soup: BeautifulSoup) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for script in soup.find_all("script"):
        kind = script.get("type") or ""
        if isinstance(kind, list):
            kind = " ".join(str(part) for part in kind)
        if "ld+json" not in str(kind).lower():
            continue
        payload = script.string if script.string is not None else script.get_text()
        payload = (payload or "").strip()
        if payload.startswith("<!--"):
            payload = payload.removeprefix("<!--").removesuffix("-->").strip()
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        _walk_jsonld(data, rows, 0)
        if len(rows) >= _EVENT_STUB_CAP:
            break
    return rows


def _attr_tokens(tag: Any, name: str) -> list[str]:
    raw = tag.get(name)
    if raw is None or raw is True:
        return []
    if isinstance(raw, str):
        return raw.split()
    if isinstance(raw, list):
        return [str(part) for part in raw]
    return [str(raw)]


def _is_h_event(tag: Any) -> bool:
    return "h-event" in _attr_tokens(tag, "class")


def _is_microdata_event(tag: Any) -> bool:
    return any(_is_schema_event(token) for token in _attr_tokens(tag, "itemtype"))


def _inside_nested(root: Any, node: Any, marker: Any) -> bool:
    """True when an ancestor between ``node`` and ``root`` matches ``marker``."""
    current = getattr(node, "parent", None)
    while current is not None and current is not root:
        if marker(current):
            return True
        current = getattr(current, "parent", None)
    return False


def _prop_text(root: Any, prop: str) -> str:
    for node in root.find_all(True):
        if prop not in _attr_tokens(node, "itemprop"):
            continue
        if _inside_nested(root, node, lambda tag: tag.has_attr("itemscope")):
            continue
        if prop == "url" and node.name == "a":
            href = node.get("href")
            if isinstance(href, str) and href.strip():
                return href.strip()
        if node.name == "time":
            when = node.get("datetime")
            if isinstance(when, str) and when.strip():
                return when.strip()
        text = node.get_text(" ", strip=True)
        if text:
            return text
    return ""


def _microdata_event_stubs(soup: BeautifulSoup) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for tag in soup.find_all(True):
        if not _is_microdata_event(tag):
            continue
        if _inside_nested(tag, tag, _is_microdata_event):
            continue
        rows.append(
            {
                "source": "schema.org",
                "name": _prop_text(tag, "name"),
                "start": _prop_text(tag, "startDate"),
                "end": _prop_text(tag, "endDate"),
                "url": _prop_text(tag, "url"),
                "location": _prop_text(tag, "location"),
            }
        )
    return rows


def _mf_text(root: Any, class_name: str) -> str:
    for node in root.find_all(True):
        if class_name not in _attr_tokens(node, "class"):
            continue
        if _inside_nested(root, node, _is_h_event):
            continue
        if class_name.startswith("dt-"):
            when = node.get("datetime")
            if isinstance(when, str) and when.strip():
                return when.strip()
        if class_name.startswith("u-") and node.name == "a":
            href = node.get("href")
            if isinstance(href, str) and href.strip():
                return href.strip()
        text = node.get_text(" ", strip=True)
        if text:
            return text
    return ""


def _h_event_stubs(soup: BeautifulSoup) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for tag in soup.find_all(True):
        if not _is_h_event(tag):
            continue
        if _inside_nested(tag, tag, _is_h_event):
            continue
        rows.append(
            {
                "source": "h-event",
                "name": _mf_text(tag, "p-name"),
                "start": _mf_text(tag, "dt-start"),
                "end": _mf_text(tag, "dt-end"),
                "url": _mf_text(tag, "u-url"),
                "location": _mf_text(tag, "p-location"),
            }
        )
    return rows


def _cap_event_stubs(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str, str]] = set()
    kept: list[dict[str, str]] = []
    for row in rows:
        if not (row.get("name") or row.get("start") or row.get("url")):
            continue
        key = (row.get("source", ""), row.get("name", ""), row.get("start", ""), row.get("url", ""))
        if key in seen:
            continue
        seen.add(key)
        kept.append({name: row.get(name, "") for name in _EVENT_STUB_KEYS})
        if len(kept) >= _EVENT_STUB_CAP:
            break
    return kept


class IntelligentAnalyzer:

    def __init__(self, client: Any = None, delta: Any = None, thresholds: Any = None):
        # Injectable for offline tests; defaults are the production client/lake.
        self.client = client if client is not None else httpx.Client(timeout=30, follow_redirects=True)
        self.delta = delta if delta is not None else get_delta()

        # Same config-driven gates as Stage2Worker (#329).
        thresholds = thresholds or stage2_quality_thresholds()
        self.MIN_WORD_COUNT = thresholds.min_word_count
        self.MIN_TEXT_TO_HTML_RATIO = thresholds.min_text_to_html_ratio
        self.MASSIVE_DOC_THRESHOLD = thresholds.massive_doc_threshold

    def analyze(self, url: str, is_heavy: bool = False) -> dict[str, Any]:
        try:
            response = self.client.get(url)

            if response.status_code >= 400:
                return self._error_record(url, response.status_code, response.reason_phrase)

            content_type = response.headers.get("content-type", "").lower()

            if "pdf" in content_type:
                return self._analyze_pdf(url, response.content, is_heavy)
            elif "html" in content_type or "text" in content_type:
                return self._analyze_html(url, response.text, is_heavy)
            else:
                return self._analyze_binary(url, response.content, content_type, is_heavy)

        except httpx.TimeoutException:
            return self._error_record(url, 0, "timeout")
        except httpx.ConnectError:
            return self._error_record(url, 0, "connection_failed")
        except Exception as e:
            return self._error_record(url, 0, f"unknown: {str(e)}")

    def _analyze_html(self, url: str, html: str, is_heavy: bool) -> dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "iframe"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()

        word_count = len(text.split())
        content_length = len(text)
        html_length = len(html)

        text_to_html_ratio = content_length / html_length if html_length > 0 else 0

        is_low_quality = word_count < self.MIN_WORD_COUNT or text_to_html_ratio < self.MIN_TEXT_TO_HTML_RATIO

        is_massive_doc = content_length > self.MASSIVE_DOC_THRESHOLD

        if is_massive_doc and not is_low_quality:
            self._route_to_stage4(url, text, word_count, content_length)
            logger.info(f"Routed large document ({content_length} chars) to Stage 4: {url[:80]}")

        keywords = []
        if not is_low_quality and not is_massive_doc:
            keywords = self._extract_keywords(text, is_heavy)

        hrefs = [str(a["href"]) for a in soup.find_all("a", href=True)]
        pdf_links = [href for href in hrefs if _is_pdf_link(href)]

        record = {
            "url": url,
            "has_error": False,
            "is_404": False,
            "error_code": 200,
            "word_count": word_count,
            "content_length": content_length,
            "html_length": html_length,
            "text_to_html_ratio": round(text_to_html_ratio, 3),
            "is_low_quality": is_low_quality,
            "is_massive_doc": is_massive_doc,
            "text_extracted": text[:10000] if not is_low_quality else "",
            "keywords": keywords,
            "has_pdf": len(pdf_links) > 0,
            "pdf_links": pdf_links[:10],
            "quality_score": self._calculate_quality_score(word_count, text_to_html_ratio),
        }
        # Parsed from the original HTML: script tags are stripped above (#648).
        events = events_json_field(html)
        if events is not None:
            record["events"] = events
        return record

    def _analyze_pdf(self, url: str, pdf_content: bytes, is_heavy: bool) -> dict[str, Any]:
        text_extracted = ""
        ocr_text = ""
        has_ocr = False

        try:
            import io

            import PyPDF2

            pdf_file = io.BytesIO(pdf_content)
            reader = PyPDF2.PdfReader(pdf_file)

            for page in reader.pages:
                # extract_text() can return None for image-only pages; one such
                # page used to raise TypeError and discard the whole document.
                text_extracted += (page.extract_text() or "") + "\n"

            text_extracted = text_extracted.strip()
        except Exception as e:
            logger.warning(f"PDF text extraction failed: {e}")

        if len(text_extracted) < 100:
            try:
                import easyocr
                from pdf2image import convert_from_bytes

                reader = easyocr.Reader(["en"], gpu=False)
                images = convert_from_bytes(pdf_content, dpi=200, fmt="jpeg")

                ocr_results = []
                for img in images[:5]:
                    result = reader.readtext(img, detail=0, paragraph=True)
                    ocr_results.extend(result)

                ocr_text = "\n".join(ocr_results).strip()
                has_ocr = True

            except Exception as e:
                logger.warning(f"PDF OCR failed: {e}")

        combined_text = (text_extracted + "\n" + ocr_text).strip()
        word_count = len(combined_text.split())

        # Same unit as the HTML path: MASSIVE_DOC_THRESHOLD is characters. It was
        # compared with the word count here, so a PDF needed ~6x more text.
        is_massive_doc = len(combined_text) > self.MASSIVE_DOC_THRESHOLD
        is_low_quality = word_count < self.MIN_WORD_COUNT

        keywords = self._extract_keywords(combined_text, is_heavy) if not is_low_quality else []

        return {
            "url": url,
            "has_error": False,
            "is_404": False,
            "error_code": 200,
            "word_count": word_count,
            "content_length": len(combined_text),
            "text_extracted": text_extracted[:5000],
            "ocr_text": ocr_text[:5000],
            "has_pdf": True,
            "has_ocr": has_ocr,
            "is_low_quality": is_low_quality,
            "is_massive_doc": is_massive_doc,
            "keywords": keywords,
            "quality_score": self._calculate_quality_score(word_count, 1.0),
        }

    def _analyze_binary(self, url: str, content: bytes, content_type: str, is_heavy: bool) -> dict[str, Any]:
        ocr_text = ""
        has_ocr = False

        if any(img_type in content_type for img_type in ["image", "jpeg", "jpg", "png", "webp"]):
            try:
                import io

                import easyocr
                from PIL import Image

                reader = easyocr.Reader(["en"], gpu=False)
                img = Image.open(io.BytesIO(content))
                result = reader.readtext(img, detail=0, paragraph=True)
                ocr_text = "\n".join(result).strip()
                has_ocr = len(ocr_text) > 0

            except Exception as e:
                logger.warning(f"Image OCR failed: {e}")

        word_count = len(ocr_text.split())
        is_low_quality = word_count < self.MIN_WORD_COUNT

        return {
            "url": url,
            "has_error": False,
            "is_404": False,
            "error_code": 200,
            "word_count": word_count,
            "content_length": len(ocr_text),
            "ocr_text": ocr_text[:5000],
            "has_ocr": has_ocr,
            "is_low_quality": is_low_quality,
            "is_massive_doc": False,
            "keywords": self._extract_keywords(ocr_text, is_heavy) if ocr_text else [],
            "quality_score": self._calculate_quality_score(word_count, 1.0),
        }

    def _error_record(self, url: str, error_code: int, error_msg: str) -> dict[str, Any]:
        return {
            "url": url,
            "has_error": True,
            "is_404": error_code == 404,
            "error_code": error_code,
            "error_message": error_msg,
            "word_count": 0,
            "is_low_quality": True,
            "is_massive_doc": False,
            "quality_score": 0,
        }

    def _extract_keywords(self, text: str, is_heavy: bool) -> list:
        if not text or len(text) < 50:
            return []

        try:
            import yake

            max_keywords = 20 if is_heavy else 10

            kw_extractor = yake.KeywordExtractor(
                lan="en",
                n=3,
                dedupLim=0.9,
                top=max_keywords,
            )

            keywords = kw_extractor.extract_keywords(text[:5000])
            return [kw[0] for kw in keywords]

        except Exception as e:
            logger.warning(f"YAKE failed: {e}")
            return []

    def _calculate_quality_score(self, word_count: int, text_ratio: float) -> float:
        word_score = min(word_count / 1000, 0.6)

        ratio_score = min(text_ratio * 0.4, 0.4)

        return round(word_score + ratio_score, 3)

    def _route_to_stage4(self, url: str, text: str, word_count: int, content_length: int):
        from datetime import datetime

        record = {
            "url": url,
            "text": text,
            "word_count": word_count,
            "content_length": content_length,
            "status": "pending",
            "queued_at": datetime.now().isoformat(),
        }

        try:
            self.delta.write("stage4_large_docs", [record], mode="append", async_write=True)
        except Exception as e:
            logger.error(f"Failed to route to Stage 4: {e}")

    def close(self):
        self.client.close()

def analyze_url(url: str, is_heavy: bool = False) -> dict[str, Any]:
    analyzer = IntelligentAnalyzer()
    try:
        return analyzer.analyze(url, is_heavy)
    finally:
        analyzer.close()
