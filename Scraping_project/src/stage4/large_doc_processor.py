import logging
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from src.stage4.pdf_sandbox import PdfQuarantined, extract_pdf_text
from src.utils.delta import get_delta

# Stage 4 HTTP metrics. These were imported from monitoring.metrics_exporter,
# which never defined them (it reports via StatsD), so the import always failed
# and the counters were silently disabled. Define them here instead.
stage4_http_requests_total: Optional["Counter"] = None
stage4_http_failures_total: Optional["Counter"] = None
try:
    from prometheus_client import Counter

    stage4_http_requests_total = Counter(
        "stage4_http_requests_total", "Stage 4 document download requests"
    )
    stage4_http_failures_total = Counter(
        "stage4_http_failures_total", "Stage 4 document download failures", ["error_type"]
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

logger = logging.getLogger(__name__)

class LargeDocProcessor:

    def __init__(self, model_name: str = "facebook/bart-large-cnn"):
        self.delta = get_delta()
        self.model_name = model_name
        self.summarizer: Any = None

        self.CHUNK_SIZE = 5000
        self.OVERLAP = 500

        self.http_client = httpx.Client(
            headers={"User-Agent": "MyScraper/1.0 (Educational Research Bot)"},
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        )

    def __del__(self):
        if hasattr(self, "http_client"):
            self.http_client.close()

    def _load_model(self):
        if self.summarizer is not None:
            return

        try:
            from transformers import pipeline

            logger.info(f"Loading heavyweight model: {self.model_name}")
            self.summarizer = pipeline(
                "summarization",
                model=self.model_name,
                device=-1,
            )
            logger.info("Model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            raise

    # A quarantined PDF (#445) is deterministic: retrying would just OOM/time out again.
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_not_exception_type(PdfQuarantined),
    )
    def _fetch_content(self, url: str, is_pdf: bool = False) -> tuple[str, str]:
        try:
            if PROMETHEUS_AVAILABLE and stage4_http_requests_total:
                stage4_http_requests_total.inc()

            response = self.http_client.get(url)
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "").lower()

            if "application/pdf" in content_type or is_pdf:
                return self._extract_pdf_text(response.content), "pdf"

            elif (
                "application/vnd.openxmlformats-officedocument.wordprocessingml" in content_type
                or url.lower().endswith(".docx")
            ):
                return self._extract_docx_text(response.content), "docx"

            elif "application/vnd.openxmlformats-officedocument.presentationml" in content_type or url.lower().endswith(
                ".pptx"
            ):
                return self._extract_pptx_text(response.content), "pptx"

            elif "application/vnd.openxmlformats-officedocument.spreadsheetml" in content_type or url.lower().endswith(
                ".xlsx"
            ):
                return self._extract_xlsx_text(response.content), "xlsx"

            elif "application/msword" in content_type or url.lower().endswith(".doc"):
                return self._extract_doc_text(response.content), "doc"

            elif "text/html" in content_type:
                return self._extract_html_text(response.text), "html"

            elif "text/plain" in content_type:
                return response.text, "txt"

            else:
                logger.warning(f"Unsupported content type: {content_type} for {url}")
                return "", "unknown"

        except PdfQuarantined:
            raise
        except httpx.HTTPStatusError as e:
            if PROMETHEUS_AVAILABLE and stage4_http_failures_total:
                stage4_http_failures_total.labels(error_type="HTTPStatusError").inc()
            logger.error(f"HTTP error fetching {url}: {e.response.status_code}")
            raise
        except httpx.RequestError as e:
            if PROMETHEUS_AVAILABLE and stage4_http_failures_total:
                stage4_http_failures_total.labels(error_type="RequestError").inc()
            logger.error(f"Request error fetching {url}: {e}")
            raise
        except Exception as e:
            if PROMETHEUS_AVAILABLE and stage4_http_failures_total:
                stage4_http_failures_total.labels(error_type="UnknownError").inc()
            logger.error(f"Unexpected error fetching {url}: {e}")
            raise

    def _extract_html_text(self, html: str) -> str:
        try:
            soup = BeautifulSoup(html, "html.parser")

            for tag in soup(["script", "style", "nav", "header", "footer", "aside", "iframe"]):
                tag.decompose()

            text = soup.get_text(separator=" ", strip=True)
            text = " ".join(text.split())

            return text
        except Exception as e:
            logger.error(f"Failed to extract HTML text: {e}")
            return ""

    def _extract_pdf_text(self, pdf_content: bytes) -> str:
        """Extract in a child process under size/RSS/time budgets (#445).

        Raises PdfQuarantined (too_large/oom/timeout/parse_error); the worker
        marks the queue row ``quarantined:<reason>`` instead of retrying it.
        """
        return extract_pdf_text(pdf_content)

    def _extract_docx_text(self, docx_content: bytes) -> str:
        try:
            from io import BytesIO

            from docx import Document

            docx_file = BytesIO(docx_content)
            doc = Document(docx_file)

            text_parts = []
            for paragraph in doc.paragraphs:
                text_parts.append(paragraph.text)

            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        text_parts.append(cell.text)

            return "\n".join(text_parts)

        except ImportError:
            logger.error("python-docx not installed - cannot extract DOCX text")
            return ""
        except Exception as e:
            logger.error(f"Failed to extract DOCX text: {e}")
            return ""

    def _extract_pptx_text(self, pptx_content: bytes) -> str:
        try:
            from io import BytesIO

            from pptx import Presentation

            pptx_file = BytesIO(pptx_content)
            prs = Presentation(pptx_file)

            text_parts = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if hasattr(shape, "text"):
                        text_parts.append(shape.text)

            return "\n".join(text_parts)

        except ImportError:
            logger.error("python-pptx not installed - cannot extract PPTX text")
            return ""
        except Exception as e:
            logger.error(f"Failed to extract PPTX text: {e}")
            return ""

    def _extract_xlsx_text(self, xlsx_content: bytes) -> str:
        try:
            from io import BytesIO

            from openpyxl import load_workbook

            xlsx_file = BytesIO(xlsx_content)
            wb = load_workbook(xlsx_file, data_only=True)

            text_parts = []
            for sheet in wb.worksheets:
                for row in sheet.iter_rows():
                    for cell in row:
                        if cell.value:
                            text_parts.append(str(cell.value))

            return "\n".join(text_parts)

        except ImportError:
            logger.error("openpyxl not installed - cannot extract XLSX text")
            return ""
        except Exception as e:
            logger.error(f"Failed to extract XLSX text: {e}")
            return ""

    def _extract_doc_text(self, doc_content: bytes) -> str:
        try:
            import tempfile

            import textract

            with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as tmp:
                tmp.write(doc_content)
                tmp_path = tmp.name

            text: str = textract.process(tmp_path).decode("utf-8")

            import os

            os.unlink(tmp_path)

            return text

        except ImportError:
            logger.error("textract not installed - cannot extract .doc text. Install: apt-get install antiword")
            return ""
        except Exception as e:
            logger.error(f"Failed to extract .doc text: {e}")
            return ""

    def process_large_document(self, url: str, text: str) -> str:
        try:
            chunks = self._split_into_chunks(text)
            logger.info(f"Split {url[:80]} into {len(chunks)} chunks")

            chunk_summaries = []
            for i, chunk in enumerate(chunks):
                try:
                    summary = self._summarize_chunk(chunk)
                    if summary:
                        chunk_summaries.append(summary)
                except Exception as e:
                    logger.warning(f"Failed to summarize chunk {i}: {e}")

            if not chunk_summaries:
                return text[:500] + "..." if len(text) > 500 else text

            combined_summary = " ".join(chunk_summaries)

            if len(combined_summary) > 1000:
                refined_summary = self._summarize_chunk(combined_summary[:5000])
                if refined_summary:
                    combined_summary = refined_summary

            return combined_summary

        except Exception as e:
            logger.error(f"Failed to process large document: {e}")
            return text[:500] + "..." if len(text) > 500 else text

    def process_queue(self) -> int:
        """One Stage 4 pass; returns summaries written (#948).

        Delegates to :class:`src.stage4.stage4_worker.Stage4Worker` so this
        legacy entry point follows the pipeline contract. The old body wrote
        summaries into ``stage4_summaries`` (Stage 3's legacy table, so Stage 3
        then skipped those URLs as already summarized), marked every pending
        row completed even when its fetch/summary failed, and rewrote the whole
        ``stage4_large_docs`` table from a stale read (dropping rows Stage 2
        appended meanwhile). The worker writes ``stage4_large_doc_summaries``,
        MERGEs per-row status and quarantines bad PDFs (#445).
        """
        import asyncio

        from src.stage4.stage4_worker import Stage4Worker

        worker = Stage4Worker.__new__(Stage4Worker)
        worker.delta = self.delta
        worker.processor = self  # reuse this instance (and any loaded model)
        return asyncio.run(worker.run())

    def _split_into_chunks(self, text: str) -> list[str]:
        if len(text) <= self.CHUNK_SIZE:
            return [text]

        chunks = []
        start = 0

        while start < len(text):
            end = start + self.CHUNK_SIZE
            chunk = text[start:end]

            if end < len(text):
                last_period = chunk.rfind(".")
                if last_period > self.CHUNK_SIZE // 2:
                    end = start + last_period + 1
                    chunk = text[start:end]

            chunks.append(chunk.strip())
            start = end - self.OVERLAP

        return chunks

    def _summarize_chunk(self, text: str) -> str | None:
        if not text or len(text) < 100:
            return None

        try:
            max_input = 1024
            if len(text) > max_input:
                text = text[:max_input]

            result = self.summarizer(text, max_length=150, min_length=30, do_sample=False)

            return str(result[0]["summary_text"])

        except Exception as e:
            logger.error(f"Chunk summarization failed: {e}")
            sentences = text.split(".")[:3]
            return ". ".join(sentences) + "."

def process_large_documents():
    processor = LargeDocProcessor()
    processor.process_queue()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    process_large_documents()
