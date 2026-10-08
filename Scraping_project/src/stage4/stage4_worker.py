import asyncio
import logging
from datetime import datetime
from typing import Any

from src.core.config import get_config
from src.utils.delta import get_delta
from src.stage4.large_doc_processor import LargeDocProcessor
from src.stage4.pdf_sandbox import PdfQuarantined
from src.otel_tracing import ensure_crawl_job_id, init_tracing, start_span

logger = logging.getLogger(__name__)

QUEUE_TABLE = "stage4_large_docs"
SUMMARY_TABLE = "stage4_large_doc_summaries"
STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
SKIP_PREFIX = "skipped:"
QUARANTINE_PREFIX = "quarantined:"
ANALYSIS_TABLE = "stage2_page_analysis"
# Pushed into the Delta scan (parquet row-group stats + predicate), so the
# fallback reads only massive-doc rows, not all of Stage 2 (#318).
MASSIVE_DOC_FILTER = [("is_massive_doc", "=", True)]

try:
    from prometheus_client import Counter

    STAGE4_ANALYSIS_ROWS_READ: Any = Counter(
        "stage4_analysis_rows_read_total",
        "stage2_page_analysis rows materialized by the Stage 4 fallback, by read mode (filtered|full_scan)",
        ["mode"],
    )
    STAGE4_CONTENT_SOURCE: Any = Counter(
        "stage4_content_source_total",
        "Where Stage 4 got a large doc's text: stage2_text (reused, no network) or fetch",
        ["source"],
    )
    STAGE4_DOCS_SELECTED: Any = Counter(
        "stage4_docs_selected_total",
        "Large docs selected for processing, by source (queue|analysis_fallback)",
        ["source"],
    )
except Exception:  # prometheus_client missing or already registered
    STAGE4_ANALYSIS_ROWS_READ = STAGE4_DOCS_SELECTED = STAGE4_CONTENT_SOURCE = None


def _count(metric: Any, n: int, **labels: str) -> None:
    if metric is not None and n:
        metric.labels(**labels).inc(n)


class Stage4Worker:

    def __init__(self, model_name: str = "facebook/bart-large-cnn", analysis_fallback: bool | None = None):
        self.delta = get_delta()
        self.processor = LargeDocProcessor(model_name=model_name)
        # stage4.analysis_fallback: false once every producer writes the queue,
        # to skip the Stage 2 scan entirely.
        if analysis_fallback is None:
            try:
                analysis_fallback = bool(get_config().get("stage4.analysis_fallback", True))
            except Exception:
                analysis_fallback = True
        self.analysis_fallback = analysis_fallback

    async def run(self) -> int:
        """Process pending large docs; returns summaries written this run."""
        init_tracing(service_name="stage4-worker")
        crawl_job_id = ensure_crawl_job_id()
        with start_span("stage4.run", stage="stage4", crawl_job_id=crawl_job_id):
            return await self._run_traced()

    # ------------------------------------------------------------------ input
    def _read(self, table: str, columns: list[str] | None = None) -> list[dict[str, Any]]:
        try:
            if columns is not None:
                return self.delta.read(table, columns=columns) or []
            return self.delta.read(table) or []
        except Exception as e:
            logger.info(f"[STAGE4] {table} unavailable: {e}")
            return []

    def _read_massive_analysis(self) -> list[dict[str, Any]]:
        """Massive-doc rows of ``stage2_page_analysis``, filtered inside the scan (#318).

        Falls back to a full read plus a Python filter only if the pushed-down
        filter can't run, e.g. an old table without ``is_massive_doc``. That
        case shows up as ``mode="full_scan"``.
        """
        manager = getattr(self.delta, "manager", None)
        if manager is not None:
            try:
                rows = manager.read(ANALYSIS_TABLE, filters=MASSIVE_DOC_FILTER) or []
                _count(STAGE4_ANALYSIS_ROWS_READ, len(rows), mode="filtered")
                return rows
            except Exception as e:
                logger.warning(f"[STAGE4] Filtered read of {ANALYSIS_TABLE} failed ({e}); falling back to full scan")
        rows = self._read(ANALYSIS_TABLE)
        _count(STAGE4_ANALYSIS_ROWS_READ, len(rows), mode="full_scan")
        return [r for r in rows if r.get("is_massive_doc", False)]

    def _pending_from_queue(self, done_urls: set[str]) -> tuple[list[dict[str, Any]], set[str]]:
        """Primary input (#611): pending rows of ``stage4_large_docs``.

        Stage 2 appends one row per routed doc, so a URL can appear more than
        once. A URL is pending only if none of its rows has left ``pending``
        and it has no summary yet. Returns (pending docs, every queued URL).
        """
        rows = self._read(QUEUE_TABLE)
        queued: set[str] = set()
        settled: set[str] = set()
        first_pending: dict[str, dict[str, Any]] = {}
        for row in rows:
            url = row.get("url")
            if not url:
                continue
            queued.add(url)
            if (row.get("status") or STATUS_PENDING) != STATUS_PENDING:
                settled.add(url)
            else:
                first_pending.setdefault(url, row)
        pending = [r for u, r in first_pending.items() if u not in settled and u not in done_urls]
        return pending, queued

    def _fallback_from_analysis(self, exclude: set[str]) -> list[dict[str, Any]]:
        """Documented fallback: massive docs in ``stage2_page_analysis`` that
        never reached the queue (analysed before routing existed, or the async
        route write was lost). The queue stays the source of truth.

        Reads only ``is_massive_doc`` rows (#318); disabled entirely by
        ``stage4.analysis_fallback: false``."""
        if not getattr(self, "analysis_fallback", True):
            return []
        return [
            doc for doc in self._read_massive_analysis()
            if not doc.get("has_error", False)
            and doc.get("url")
            and doc["url"] not in exclude
        ]

    # -------------------------------------------------------------------- run
    async def _run_traced(self) -> int:
        written = 0
        logger.info("[STAGE4] Worker starting for large document processing")

        # Only the url column: the summaries themselves are large text (#318).
        done_urls = {r["url"] for r in self._read(SUMMARY_TABLE, columns=["url"]) if r.get("url")}
        queue_docs, queued_urls = self._pending_from_queue(done_urls)
        fallback_docs = self._fallback_from_analysis(queued_urls | done_urls)
        _count(STAGE4_DOCS_SELECTED, len(queue_docs), source="queue")
        _count(STAGE4_DOCS_SELECTED, len(fallback_docs), source="analysis_fallback")
        logger.info(
            f"[STAGE4] {len(queue_docs)} pending in {QUEUE_TABLE}; "
            f"{len(fallback_docs)} unqueued massive docs from stage2_page_analysis (fallback)"
        )

        work = [(doc, True) for doc in queue_docs] + [(doc, False) for doc in fallback_docs]
        if not work:
            logger.info("[STAGE4] No large documents to process")
            return written

        results: list[dict[str, Any]] = []
        completed: list[str] = []
        skipped: dict[str, str] = {}
        quarantined: dict[str, str] = {}
        for i, (doc, from_queue) in enumerate(work):
            if _drain_requested(i, len(work)):
                break  # #325: results so far are still saved and acked below
            url = doc.get("url", "")
            logger.info(f"[STAGE4] Processing {i+1}/{len(work)}: {url[:80]}")
            try:
                result, skip_reason = await self._process_large_document(doc)
            except PdfQuarantined as e:  # #445: OOM/timeout/oversized PDF never stays pending
                logger.warning(f"[STAGE4] Quarantined {url}: {e}")
                if from_queue:
                    quarantined[url] = e.reason
                continue
            except Exception as e:  # transient: stays pending, retried next run
                logger.error(f"[STAGE4] Failed to process {url}: {e}")
                continue
            if result:
                results.append(result)
                if from_queue:
                    completed.append(url)
            elif skip_reason and from_queue:
                skipped[url] = skip_reason

        if results:
            try:
                self.delta.write(SUMMARY_TABLE, results, mode="append", async_write=False)
                written = len(results)
                logger.info(f"[STAGE4]  Saved {len(results)} large document summaries")
            except Exception as e:
                logger.error(f"[STAGE4] Failed to save results: {e}")
                completed = []  # summaries not durable: leave those rows pending

        self._mark_queue(completed, skipped, quarantined)
        logger.info("[STAGE4] Worker completed")
        return written

    def _mark_queue(
        self, completed: list[str], skipped: dict[str, str], quarantined: dict[str, str] | None = None
    ) -> None:
        """Row-level MERGE of queue status, like Stage 2's queue (#611).

        ``completed``, ``skipped:<reason>`` or ``quarantined:<reason>`` (#445).
        The reason lives in ``status``
        because MERGE can't add new columns to an existing queue table.
        """
        updates = [{"url": u, "status": STATUS_COMPLETED} for u in dict.fromkeys(completed)]
        updates += [{"url": u, "status": f"{SKIP_PREFIX}{why}"} for u, why in skipped.items()
                    if u not in set(completed)]
        updates += [{"url": u, "status": f"{QUARANTINE_PREFIX}{why}"} for u, why in (quarantined or {}).items()
                    if u not in set(completed)]
        if not updates:
            return
        n = self.delta.merge_into(QUEUE_TABLE, updates, "url", ["status"])
        if n < 0:
            logger.error(f"[STAGE4] Could not update status for {len(updates)} queue rows; they stay pending")
        else:
            logger.info(
                f"[STAGE4] Queue: {len(completed)} completed, {len(skipped)} skipped, "
                f"{len(quarantined or {})} quarantined"
            )

    @staticmethod
    def _stored_text(doc: dict[str, Any]) -> str | None:
        """Stage 2's extracted text, if it is the *complete* body (#320).

        Stage 2 writes the full text onto the queue row when routing (up to
        ``stage4.inline_text_max_chars``). ``stage2_page_analysis.text_content``
        is cut at 10k chars, so a fallback row whose text is shorter than its
        ``content_length`` is truncated and must be re-fetched. Summarizing a
        prefix would silently drop most of the document.
        """
        text = doc.get("text_content")
        if not isinstance(text, str) or not text.strip():
            return None
        expected = doc.get("content_length")
        if isinstance(expected, int) and expected > 0 and len(text) < expected:
            return None
        return text

    async def _process_large_document(
        self, doc: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Returns (summary row, None) on success or (None, skip_reason)."""
        url = doc.get("url", "")
        url_hash = doc.get("url_hash", "")
        # Stage 2's PDF route sets is_pdf on the queue row. Fallback rows from
        # stage2_page_analysis carry no PDF flag in their schema, so use the URL.
        is_pdf = (
            bool(doc.get("is_pdf"))
            or doc.get("content_hint") == "pdf"
            or url.lower().split("?", 1)[0].endswith(".pdf")
        )

        stored = None if is_pdf else self._stored_text(doc)
        if stored is not None:
            text, content_type = stored, doc.get("content_type") or "html"
            _count(STAGE4_CONTENT_SOURCE, 1, source="stage2_text")
            logger.info(f"[STAGE4] Reusing Stage 2 text for {url[:80]} (no re-fetch)")
        else:
            logger.info(f"[STAGE4] Fetching content from {url[:80]}")
            text, content_type = self.processor._fetch_content(url, is_pdf=is_pdf)
            _count(STAGE4_CONTENT_SOURCE, 1, source="fetch")

        if not text:
            logger.warning(f"[STAGE4] No text content for {url[:80]}")
            return None, "no_text"

        logger.info(f"[STAGE4] Processing {len(text)} characters")
        summary = self.processor.process_large_document(url, text)

        if not summary:
            logger.warning(f"[STAGE4] No summary generated for {url[:80]}")
            return None, "no_summary"

        return {
            "url": url,
            "url_hash": url_hash,
            "summary": summary,
            "content_type": content_type,
            "original_size": len(text),
            "summary_size": len(summary),
            "compression_ratio": round(len(summary) / len(text), 3) if len(text) > 0 else 0,
            "is_pdf": is_pdf,
            "processed_at": datetime.now().isoformat(),
        }, None

def _drain_requested(done: int, total: int) -> bool:
    """SIGTERM/SIGINT seen: stop before the next document (#325)."""
    from src.utils.graceful_shutdown import shutdown_requested

    if not shutdown_requested():
        return False
    logger.info(f"[STAGE4] Shutdown requested: stopping after {done}/{total}; the rest stays pending")
    return True


async def run_stage4_worker(shutdown=None):
    from src.utils.graceful_shutdown import run_drain_loop

    logger.info("[STAGE4] Worker starting in continuous mode...")

    async def run_once():
        await Stage4Worker().run()

    await run_drain_loop("stage4", run_once, idle_seconds=60, error_seconds=30, shutdown=shutdown)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    from src.utils.worker_metrics import start_worker_metrics_server

    start_worker_metrics_server("stage4")  # #789
    asyncio.run(run_stage4_worker())
