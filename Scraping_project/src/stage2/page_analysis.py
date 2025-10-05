"""
Stage 2: Deep page analysis - word count, content size, errors, PDF+OCR+text, YAKE keywords.
"""

import re
import logging
import mimetypes
from pathlib import Path
from typing import Dict, List, Any, Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class PageAnalyzer:
    """Analyze page content, detect errors, extract text from PDFs, run YAKE."""

    def __init__(self):
        self.client = httpx.Client(timeout=30, follow_redirects=True)

    def analyze(self, url: str, is_heavy: bool = False) -> Dict[str, Any]:
        """Complete page analysis."""
        try:
            response = self.client.get(url)

            # Error detection
            if response.status_code >= 400:
                return self._error_record(url, response.status_code, response.reason_phrase)

            content_type = response.headers.get('content-type', '').lower()

            # Route based on content type
            if 'pdf' in content_type:
                return self._analyze_pdf(url, response.content, is_heavy)
            elif 'html' in content_type or 'text' in content_type:
                return self._analyze_html(url, response.text, is_heavy)
            else:
                return self._analyze_binary(url, response.content, content_type, is_heavy)

        except httpx.TimeoutException:
            return self._error_record(url, 0, 'timeout')
        except httpx.ConnectError:
            return self._error_record(url, 0, 'connection_failed')
        except Exception as e:
            return self._error_record(url, 0, f'unknown: {str(e)}')

    def _error_record(self, url: str, error_code: int, error_msg: str) -> Dict[str, Any]:
        """Create error record."""
        is_404 = error_code == 404
        is_server_error = 500 <= error_code < 600

        return {
            'url': url,
            'has_error': True,
            'is_404': is_404,
            'is_server_error': is_server_error,
            'error_code': error_code,
            'error_message': error_msg,
            'word_count': 0,
            'content_length': 0,
            'text_extracted': '',
            'keywords': [],
            'has_pdf': False,
            'has_ocr': False,
        }

    def _analyze_html(self, url: str, html: str, is_heavy: bool) -> Dict[str, Any]:
        """Analyze HTML page."""
        soup = BeautifulSoup(html, 'html.parser')

        # Remove scripts, styles, nav, footer
        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe']):
            tag.decompose()

        # Extract text
        text = soup.get_text(separator=' ', strip=True)
        text = re.sub(r'\s+', ' ', text).strip()

        # Word count
        words = text.split()
        word_count = len(words)

        # Content length
        content_length = len(text)

        # Extract keywords with YAKE
        keywords = self._extract_keywords(text, is_heavy)

        # Check for embedded PDFs
        pdf_links = [a['href'] for a in soup.find_all('a', href=True) if a['href'].endswith('.pdf')]
        has_pdf = len(pdf_links) > 0

        return {
            'url': url,
            'has_error': False,
            'is_404': False,
            'is_server_error': False,
            'error_code': 200,
            'error_message': '',
            'word_count': word_count,
            'content_length': content_length,
            'text_extracted': text[:10000],  # First 10k chars
            'keywords': keywords,
            'has_pdf': has_pdf,
            'pdf_links': pdf_links[:10],  # Max 10 PDF links
            'has_ocr': False,
            'ocr_text': '',
        }

    def _analyze_pdf(self, url: str, pdf_content: bytes, is_heavy: bool) -> Dict[str, Any]:
        """Analyze PDF with text extraction + OCR."""
        text_extracted = ''
        ocr_text = ''
        has_ocr = False

        # Try text extraction first
        try:
            import PyPDF2
            import io

            pdf_file = io.BytesIO(pdf_content)
            reader = PyPDF2.PdfReader(pdf_file)

            for page in reader.pages:
                text_extracted += page.extract_text() + '\n'

            text_extracted = text_extracted.strip()
        except Exception as e:
            logger.warning(f"PDF text extraction failed for {url}: {e}")

        # If text extraction failed or minimal text, try OCR
        if len(text_extracted) < 100:
            try:
                import easyocr
                import io
                from pdf2image import convert_from_bytes

                reader = easyocr.Reader(['en'], gpu=False)
                images = convert_from_bytes(pdf_content, dpi=200, fmt='jpeg')

                ocr_results = []
                for img in images[:5]:  # Max 5 pages for OCR
                    result = reader.readtext(img, detail=0, paragraph=True)
                    ocr_results.extend(result)

                ocr_text = '\n'.join(ocr_results).strip()
                has_ocr = True

            except Exception as e:
                logger.warning(f"PDF OCR failed for {url}: {e}")

        # Combine text + OCR
        combined_text = (text_extracted + '\n' + ocr_text).strip()

        # Word count
        words = combined_text.split()
        word_count = len(words)

        # Keywords
        keywords = self._extract_keywords(combined_text, is_heavy)

        return {
            'url': url,
            'has_error': False,
            'is_404': False,
            'is_server_error': False,
            'error_code': 200,
            'error_message': '',
            'word_count': word_count,
            'content_length': len(combined_text),
            'text_extracted': text_extracted[:5000],
            'ocr_text': ocr_text[:5000],
            'has_pdf': True,
            'pdf_links': [],
            'has_ocr': has_ocr,
            'keywords': keywords,
        }

    def _analyze_binary(self, url: str, content: bytes, content_type: str, is_heavy: bool) -> Dict[str, Any]:
        """Analyze binary/image files with OCR."""
        ocr_text = ''
        has_ocr = False

        # Try OCR on images
        if any(img_type in content_type for img_type in ['image', 'jpeg', 'jpg', 'png', 'gif', 'webp']):
            try:
                import easyocr
                import io
                from PIL import Image

                reader = easyocr.Reader(['en'], gpu=False)
                img = Image.open(io.BytesIO(content))
                result = reader.readtext(img, detail=0, paragraph=True)
                ocr_text = '\n'.join(result).strip()
                has_ocr = len(ocr_text) > 0

            except Exception as e:
                logger.warning(f"Image OCR failed for {url}: {e}")

        # Word count from OCR
        words = ocr_text.split()
        word_count = len(words)

        # Keywords
        keywords = self._extract_keywords(ocr_text, is_heavy) if ocr_text else []

        return {
            'url': url,
            'has_error': False,
            'is_404': False,
            'is_server_error': False,
            'error_code': 200,
            'error_message': '',
            'word_count': word_count,
            'content_length': len(ocr_text),
            'text_extracted': '',
            'ocr_text': ocr_text[:5000],
            'has_pdf': False,
            'pdf_links': [],
            'has_ocr': has_ocr,
            'keywords': keywords,
        }

    def _extract_keywords(self, text: str, is_heavy: bool) -> List[str]:
        """Extract keywords using YAKE."""
        if not text or len(text) < 50:
            return []

        try:
            import yake

            # Adjust keyword count based on content size
            max_keywords = 20 if is_heavy else 10

            kw_extractor = yake.KeywordExtractor(
                lan="en",
                n=3,  # Max 3-word phrases
                dedupLim=0.9,
                top=max_keywords,
                features=None
            )

            keywords = kw_extractor.extract_keywords(text[:5000])  # First 5k chars
            return [kw[0] for kw in keywords]

        except Exception as e:
            logger.warning(f"YAKE extraction failed: {e}")
            return []

    def close(self):
        """Close HTTP client."""
        self.client.close()


def analyze_url(url: str, is_heavy: bool = False) -> Dict[str, Any]:
    """Convenience function to analyze a single URL."""
    analyzer = PageAnalyzer()
    try:
        return analyzer.analyze(url, is_heavy)
    finally:
        analyzer.close()
