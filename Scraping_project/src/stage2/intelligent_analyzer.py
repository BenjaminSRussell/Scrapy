"""Stage 2: Intelligent Page Analysis with Quality Control and Triage.
Routes massive documents to separate queue for Stage 4 processing.
"""

import logging
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from src.common.delta_lake import get_delta_manager

logger = logging.getLogger(__name__)


class IntelligentAnalyzer:
    """Advanced page analysis with quality control and document triage."""

    def __init__(self):
        self.client = httpx.Client(timeout=30, follow_redirects=True)
        self.delta = get_delta_manager()

        # Quality thresholds
        self.MIN_WORD_COUNT = 50
        self.MIN_TEXT_TO_HTML_RATIO = 0.1
        self.MASSIVE_DOC_THRESHOLD = 50000  # 50k characters (roughly 8-10k words)

    def analyze(self, url: str, is_heavy: bool = False) -> dict[str, Any]:
        """Complete analysis with QC and triage."""
        try:
            response = self.client.get(url)

            if response.status_code >= 400:
                return self._error_record(
                    url, response.status_code, response.reason_phrase
                )

            content_type = response.headers.get("content-type", "").lower()

            if "pdf" in content_type:
                return self._analyze_pdf(url, response.content, is_heavy)
            elif "html" in content_type or "text" in content_type:
                return self._analyze_html(url, response.text, is_heavy)
            else:
                return self._analyze_binary(
                    url, response.content, content_type, is_heavy
                )

        except httpx.TimeoutException:
            return self._error_record(url, 0, "timeout")
        except httpx.ConnectError:
            return self._error_record(url, 0, "connection_failed")
        except Exception as e:
            return self._error_record(url, 0, f"unknown: {str(e)}")

    def _analyze_html(self, url: str, html: str, is_heavy: bool) -> dict[str, Any]:
        """Analyze HTML with quality control."""
        soup = BeautifulSoup(html, "html.parser")

        # Remove noise
        for tag in soup(
            ["script", "style", "nav", "header", "footer", "aside", "iframe"]
        ):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()

        # Quality control
        word_count = len(text.split())
        content_length = len(text)
        html_length = len(html)

        text_to_html_ratio = content_length / html_length if html_length > 0 else 0

        # Quality checks
        is_low_quality = (
            word_count < self.MIN_WORD_COUNT
            or text_to_html_ratio < self.MIN_TEXT_TO_HTML_RATIO
        )

        # Document triage - check if text is massive
        is_massive_doc = content_length > self.MASSIVE_DOC_THRESHOLD

        # Route massive documents to Stage 4 queue
        if is_massive_doc and not is_low_quality:
            self._route_to_stage4(url, text, word_count, content_length)
            logger.info(
                f"Routed large document ({content_length} chars) to Stage 4: {url[:80]}"
            )

        # Extract keywords only if quality passes and not massive
        keywords = []
        if not is_low_quality and not is_massive_doc:
            keywords = self._extract_keywords(text, is_heavy)

        # Check for embedded PDFs
        pdf_links = [
            a["href"]
            for a in soup.find_all("a", href=True)
            if a["href"].endswith(".pdf")
        ]

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
            "quality_score": self._calculate_quality_score(
                word_count, text_to_html_ratio
            ),
        }

    def _analyze_pdf(
        self, url: str, pdf_content: bytes, is_heavy: bool
    ) -> dict[str, Any]:
        """Analyze PDF with text + OCR."""
        text_extracted = ""
        ocr_text = ""
        has_ocr = False

        # Try text extraction
        try:
            import io

            import PyPDF2

            pdf_file = io.BytesIO(pdf_content)
            reader = PyPDF2.PdfReader(pdf_file)

            for page in reader.pages:
                text_extracted += page.extract_text() + "\n"

            text_extracted = text_extracted.strip()
        except Exception as e:
            logger.warning(f"PDF text extraction failed: {e}")

        # OCR if minimal text
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

        is_massive_doc = word_count > self.MASSIVE_DOC_THRESHOLD
        is_low_quality = word_count < self.MIN_WORD_COUNT

        keywords = (
            self._extract_keywords(combined_text, is_heavy)
            if not is_low_quality
            else []
        )

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

    def _analyze_binary(
        self, url: str, content: bytes, content_type: str, is_heavy: bool
    ) -> dict[str, Any]:
        """Analyze images with OCR."""
        ocr_text = ""
        has_ocr = False

        if any(
            img_type in content_type
            for img_type in ["image", "jpeg", "jpg", "png", "webp"]
        ):
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

    def _error_record(
        self, url: str, error_code: int, error_msg: str
    ) -> dict[str, Any]:
        """Create error record."""
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
        """Extract keywords with YAKE."""
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
        """Calculate quality score 0-1."""
        # Word count component (0-0.6)
        word_score = min(word_count / 1000, 0.6)

        # Text ratio component (0-0.4)
        ratio_score = min(text_ratio * 0.4, 0.4)

        return round(word_score + ratio_score, 3)

    def _route_to_stage4(
        self, url: str, text: str, word_count: int, content_length: int
    ):
        """Route large document to Stage 4 for heavyweight processing."""
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
            self.delta.write(
                "stage4_large_docs", [record], mode="append", async_write=True
            )
        except Exception as e:
            logger.error(f"Failed to route to Stage 4: {e}")

    def close(self):
        """Close HTTP client."""
        self.client.close()


def analyze_url(url: str, is_heavy: bool = False) -> dict[str, Any]:
    """Convenience function."""
    analyzer = IntelligentAnalyzer()
    try:
        return analyzer.analyze(url, is_heavy)
    finally:
        analyzer.close()
