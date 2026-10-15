import importlib
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

# Crossref-style DOI. Suffix punctuation from the surrounding sentence is stripped (#651).
_DOI_BODY = r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+"
_DOI_FIND = re.compile(rf"(?i)(?<![\w.])({_DOI_BODY})")
_DOI_EXACT = re.compile(rf"(?i)^{_DOI_BODY}$")
_YEAR_RE = re.compile(r"\b(?:1[6-9]\d{2}|20\d{2})\b")
_DOI_CLOSER = {")": "(", "]": "[", "}": "{", ">": "<"}
_SCAN_LIMIT = 500_000
_PARSER_LIMIT = 20_000
_MAX_CITATIONS = 100
_META_DOI = ("citation_doi", "dc.identifier.doi", "prism.doi", "doi", "dc.identifier")
_META_TITLE = ("citation_title", "dc.title", "prism.title")
_META_AUTHOR = ("citation_author", "citation_authors", "dc.creator")
_META_JOURNAL = ("citation_journal_title", "prism.publicationname")
_META_DATE = ("citation_publication_date", "citation_date", "prism.publicationdate", "dc.date")

def _is_pdf_link(href: str) -> bool:
    """``.pdf`` by path extension, so ``Report.PDF`` and ``a.pdf?dl=1#p2`` count (#221)."""
    try:
        path = urlparse(href).path
    except ValueError:
        return False
    return posixpath.splitext(path)[1].lower() == ".pdf"


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return ""


def _strip_doi(raw: str) -> str:
    doi = raw.strip()
    while doi:
        last = doi[-1]
        if last in ".,;:":
            doi = doi[:-1]
            continue
        opener = _DOI_CLOSER.get(last)
        if opener is not None and doi.count(last) > doi.count(opener):
            doi = doi[:-1]
            continue
        break
    return doi


def _normalize_doi(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    match = _DOI_FIND.search(value.strip())
    if match is None:
        return ""
    doi = _strip_doi(match.group(1))
    if not doi or len(doi) > 256 or _DOI_EXACT.fullmatch(doi) is None:
        return ""
    return doi


def _dois_in(blob: str) -> list[str]:
    if not blob:
        return []
    found: list[str] = []
    for match in _DOI_FIND.finditer(blob[:_SCAN_LIMIT]):
        doi = _strip_doi(match.group(1))
        if doi and len(doi) <= 256 and _DOI_EXACT.fullmatch(doi):
            found.append(doi)
    return found


def _year(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    match = _YEAR_RE.search(value)
    return match.group(0) if match else ""


def _authors(value: Any) -> list[str]:
    items: list[Any]
    if value is None:
        return []
    if isinstance(value, str):
        items = [part.strip() for part in re.split(r"\s*;\s*", value) if part.strip()]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return []
    names: list[str] = []
    for item in items:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            named = item.get("name") or item.get("author")
            if isinstance(named, str) and named.strip():
                text = named.strip()
            else:
                given = item.get("given") or item.get("first") or ""
                family = item.get("family") or item.get("last") or ""
                text = f"{given} {family}".strip() if isinstance(given, str) and isinstance(family, str) else ""
        else:
            text = ""
        if text:
            names.append(text[:300])
        if len(names) >= 30:
            break
    return names


def _citation(
    *,
    doi: str = "",
    title: str = "",
    authors: list[str] | None = None,
    journal: str = "",
    year: str = "",
    raw: str = "",
    source: str,
) -> dict[str, Any]:
    return {
        "doi": doi,
        "title": title[:500],
        "authors": list(authors or []),
        "journal": journal[:300],
        "year": year,
        "raw": raw[:500],
        "source": source,
    }


def _load_scholarly_parser() -> Any:
    """Optional citation parser. Missing install is normal; search/network APIs are never called (#651)."""
    try:
        module = importlib.import_module("scholarly")
    except Exception:
        return None
    for name in ("parse_citations", "parse_citation"):
        parser = getattr(module, name, None)
        if callable(parser):
            return parser
    return None


def _meta_groups(soup: Any) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    if soup is None or not hasattr(soup, "find_all"):
        return grouped
    try:
        tags = soup.find_all("meta")
    except Exception:
        return grouped
    for tag in tags:
        try:
            key = tag.get("name") or tag.get("property") or tag.get("itemprop") or ""
            content = tag.get("content")
        except Exception:
            continue
        key_text = str(key).strip().lower()
        if not key_text or not isinstance(content, str) or not content.strip():
            continue
        grouped.setdefault(key_text, []).append(content.strip())
    return grouped


def _first_meta(grouped: dict[str, list[str]], names: tuple[str, ...]) -> str:
    for name in names:
        values = grouped.get(name)
        if values:
            return values[0]
    return ""


def _all_meta(grouped: dict[str, list[str]], names: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for name in names:
        values.extend(grouped.get(name) or [])
    return values


def _soup_for(html: str, soup: Any) -> Any:
    if soup is not None and hasattr(soup, "find_all"):
        return soup
    if not html.strip():
        return None
    try:
        return BeautifulSoup(html, "html.parser")
    except Exception:
        return None


def _parser_text(html: str, text: str, soup: Any) -> str:
    if text.strip():
        return re.sub(r"\s+", " ", text).strip()[:_PARSER_LIMIT]
    if soup is not None:
        try:
            visible = soup.get_text(separator=" ", strip=True)
        except Exception:
            visible = ""
        if isinstance(visible, str) and visible.strip():
            return re.sub(r"\s+", " ", visible).strip()[:_PARSER_LIMIT]
    if html.strip():
        rough = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", rough).strip()[:_PARSER_LIMIT]
    return ""


def _coerce_parser_item(item: Any) -> dict[str, Any] | None:
    if isinstance(item, str):
        doi = _normalize_doi(item)
        if not doi:
            return None
        return _citation(doi=doi, raw=item.strip(), source="scholarly")
    if not isinstance(item, dict):
        return None
    raw_value = item.get("raw") or item.get("citation") or ""
    raw = raw_value.strip() if isinstance(raw_value, str) else ""
    doi = _normalize_doi(item.get("doi") or item.get("DOI") or "") or _normalize_doi(raw)
    title_value = item.get("title") or item.get("Title") or ""
    title = title_value.strip() if isinstance(title_value, str) else ""
    journal_value = item.get("journal") or item.get("container-title") or item.get("venue") or ""
    journal = journal_value.strip() if isinstance(journal_value, str) else ""
    authors = _authors(item.get("authors") if item.get("authors") is not None else item.get("author"))
    year = _year(item.get("year") if isinstance(item.get("year"), str) else None)
    if not year:
        dated = item.get("date") or item.get("published") or item.get("year")
        year = _year(dated if isinstance(dated, str) else str(dated) if isinstance(dated, int) else "")
    if not any((doi, title, authors, raw)):
        return None
    return _citation(doi=doi, title=title, authors=authors, journal=journal, year=year, raw=raw, source="scholarly")


def _parser_citations(text: str) -> list[dict[str, Any]]:
    if not text.strip():
        return []
    parser = _load_scholarly_parser()
    if not callable(parser):
        return []
    try:
        parsed = parser(text)
    except Exception as exc:
        logger.warning(f"optional scholarly citation parser failed: {exc}")
        return []
    if parsed is None:
        return []
    if isinstance(parsed, (dict, str)):
        parsed = [parsed]
    if not isinstance(parsed, (list, tuple)):
        return []
    rows: list[dict[str, Any]] = []
    for item in parsed:
        row = _coerce_parser_item(item)
        if row is not None:
            rows.append(row)
    return rows


def _dedupe_key(row: dict[str, Any]) -> tuple[str, str] | None:
    doi = str(row.get("doi") or "").lower()
    if doi:
        return ("doi", doi)
    title = str(row.get("title") or "").strip().lower()
    raw = str(row.get("raw") or "").strip().lower()
    authors = ",".join(str(name).strip().lower() for name in row.get("authors") or [])
    if not title and not raw and not authors:
        return None
    return ("other", f"{title}|{raw}|{authors}")


def extract_citations(html: Any = None, text: Any = None, soup: Any = None) -> list[dict[str, Any]]:
    """Publication citations as a JSON-ready list. Empty or unreadable input is ``[]`` (#651)."""
    try:
        return _extract_citations(html, text, soup)
    except Exception as exc:
        logger.warning(f"citation extraction failed: {exc}")
        return []


def _extract_citations(html: Any, text: Any, soup: Any) -> list[dict[str, Any]]:
    html_text = _as_text(html)
    body_text = _as_text(text)
    if not html_text.strip() and not body_text.strip() and soup is None:
        return []
    parsed_soup = _soup_for(html_text, soup)
    grouped = _meta_groups(parsed_soup)
    rows: list[dict[str, Any]] = []

    doi = ""
    raw_meta = ""
    for name in _META_DOI:
        for value in grouped.get(name) or []:
            doi = _normalize_doi(value)
            if doi:
                raw_meta = value
                break
        if doi:
            break
    title = _first_meta(grouped, _META_TITLE)
    authors = _authors(_all_meta(grouped, _META_AUTHOR))
    journal = _first_meta(grouped, _META_JOURNAL)
    year = _year(_first_meta(grouped, _META_DATE))
    if doi or ("citation_title" in grouped and title and authors):
        rows.append(_citation(
            doi=doi,
            title=title,
            authors=authors,
            journal=journal,
            year=year,
            raw=raw_meta,
            source="meta",
        ))

    scanned: set[str] = set()
    for blob in (html_text, body_text):
        if not blob or blob in scanned:
            continue
        scanned.add(blob)
        for found in _dois_in(blob):
            rows.append(_citation(doi=found, raw=found, source="doi"))

    rows.extend(_parser_citations(_parser_text(html_text, body_text, parsed_soup)))

    kept: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = _dedupe_key(row)
        if key is None or key in seen:
            continue
        seen.add(key)
        kept.append(row)
        if len(kept) >= _MAX_CITATIONS:
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
        citations = extract_citations(html, text, soup)

        return {
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
            "citations": citations,
            "quality_score": self._calculate_quality_score(word_count, text_to_html_ratio),
        }

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
            "citations": extract_citations(text=combined_text),
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
            "citations": extract_citations(text=ocr_text) if ocr_text else [],
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
            "citations": [],
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
