import asyncio
import logging
from datetime import datetime
from typing import Any

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


class Stage4Worker:

    def __init__(self, model_name: str = "facebook/bart-large-cnn"):
        self.delta = get_delta()
        self.processor = LargeDocProcessor(model_name=model_name)

    async def run(self) -> int:
        """Process pending large docs; returns summaries written this run."""
        init_tracing(service_name="stage4-worker")
        crawl_job_id = ensure_crawl_job_id()
        with start_span("stage4.run", stage="stage4", crawl_job_id=crawl_job_id):
            return await self._run_traced()

    # ------------------------------------------------------------------ input
    def _read(self, table: str) -> list[dict[str, Any]]:
        try:
            return self.delta.read(table) or []
        except Exception as e:
            logger.info(f"[STAGE4] {table} unavailable: {e}")
            return []

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
        route write was lost). The queue stays the source of truth."""
        return [
            doc for doc in self._read("stage2_page_analysis")
            if doc.get("is_massive_doc", False)
            and not doc.get("has_error", False)
            and doc.get("url")
            and doc["url"] not in exclude
        ]

    # -------------------------------------------------------------------- run
    async def _run_traced(self) -> int:
        written = 0
        logger.info("[STAGE4] Worker starting for large document processing")

        done_urls = {r["url"] for r in self._read(SUMMARY_TABLE) if r.get("url")}
        queue_docs, queued_urls = self._pending_from_queue(done_urls)
        fallback_docs = self._fallback_from_analysis(queued_urls | done_urls)
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

        logger.info(f"[STAGE4] Fetching content from {url[:80]}")
        text, content_type = self.processor._fetch_content(url, is_pdf=is_pdf)

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

async def run_stage4_worker():
    logger.info("[STAGE4] Worker starting in continuous mode...")

    while True:
        try:
            worker = Stage4Worker()
            await worker.run()

            logger.info("[STAGE4] Waiting 60 seconds before next check...")
            await asyncio.sleep(60)

        except KeyboardInterrupt:
            logger.info("[STAGE4] Worker shutting down...")
            break
        except Exception as e:
            logger.error(f"[STAGE4] Error in worker loop: {e}")
            await asyncio.sleep(30)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    from src.utils.worker_metrics import start_worker_metrics_server

    start_worker_metrics_server("stage4")  # #789
    asyncio.run(run_stage4_worker())
