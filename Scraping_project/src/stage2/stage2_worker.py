import asyncio
import logging
import os
import time
from collections import Counter as TallyCounter
from datetime import datetime
from typing import Any

import aiohttp
import pyarrow as pa
from bs4 import BeautifulSoup
from deltalake import DeltaTable

from src.core.config import stage_worker_settings
from src.core.constants import TABLE_STAGE2_ERRORS
from src.utils.delta import get_delta
from src.utils.postgres import get_postgres_manager
from src.otel_tracing import ensure_crawl_job_id, init_tracing, start_span

logger = logging.getLogger(__name__)

try:  # accepted vs quarantined Stage 2 rows (#331)
    from prometheus_client import Counter

    STAGE2_ROWS = Counter(
        "stage2_rows_total",
        "Stage 2 rows by outcome: accepted (stage2_page_analysis) or quarantined (stage2_errors).",
        ["outcome"],
    )
except Exception:  # prometheus_client missing or metric already registered
    STAGE2_ROWS = None

try:  # exceptions escaping _analyze_url inside gather (#214)
    from prometheus_client import Counter as _Counter

    STAGE2_GATHER_EXCEPTIONS = _Counter(
        "stage2_gather_exceptions_total",
        "Exceptions raised out of Stage 2 URL analysis inside asyncio.gather.",
        ["exception"],
    )
except Exception:
    STAGE2_GATHER_EXCEPTIONS = None


def split_stage2_results(results: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a batch into (accepted analysis rows, quarantined error rows) (#331)."""
    rows = [r for r in results if isinstance(r, dict)]
    accepted = [r for r in rows if not r.get("has_error")]
    quarantined = [r for r in rows if r.get("has_error")]
    return accepted, quarantined


DEFAULT_STAGE2_MAX_RETRIES = 3


def _is_terminal_error(row: dict[str, Any]) -> bool:
    """Errors that retrying cannot fix: bad URLs and 4xx other than 408/429."""
    if row.get("error_message") == "invalid_url":
        return True
    code = int(row.get("error_code") or 0)
    return 400 <= code < 500 and code not in (408, 429)


def plan_queue_updates(
    valid_results: list[dict[str, Any]],
    prior_failures: dict[str, int],
    max_retries: int,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Decide queue transitions for a batch (#160).

    Returns (completed_urls, failed_urls, retry_rows). Successful rows complete;
    error rows stay ``pending`` until they have failed ``max_retries`` times in
    total (prior runs + this one) or hit a terminal error, then become ``failed``.
    ``prior_failures`` is updated in place with this batch's failures.
    """
    completed: list[str] = []
    failed: list[str] = []
    retrying: list[dict[str, Any]] = []
    for row in valid_results:
        url = row.get("url")
        if not url:
            continue
        if not row.get("has_error"):
            completed.append(url)
            continue
        attempts = prior_failures.get(url, 0) + 1
        prior_failures[url] = attempts
        row["_retry_count"] = attempts
        if attempts >= max_retries or _is_terminal_error(row):
            failed.append(url)
        else:
            retrying.append(row)
    return completed, failed, retrying


class Stage2Worker:

    def __init__(self, max_concurrent: int = 50, batch_size: int = 100):
        self.max_concurrent = max_concurrent
        self.batch_size = batch_size
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.delta = get_delta()
        self.postgres = get_postgres_manager()

        self.MIN_WORD_COUNT = 50
        self.MIN_TEXT_TO_HTML_RATIO = 0.1
        self.MASSIVE_DOC_THRESHOLD = 50000

        self.perf_start_time = None
        self.perf_urls_processed = 0

        try:
            self.max_retries = max(1, int(os.getenv("STAGE2_MAX_RETRIES", DEFAULT_STAGE2_MAX_RETRIES)))
        except ValueError:
            self.max_retries = DEFAULT_STAGE2_MAX_RETRIES
        self._dlq: Any = None

    def _load_prior_failures(self) -> dict[str, int]:
        """Per-URL failure counts from earlier runs, from the stage2_errors quarantine."""
        try:
            rows = self.delta.read_table(TABLE_STAGE2_ERRORS)
            if hasattr(rows, "to_pylist"):
                rows = rows.to_pylist()
            return dict(TallyCounter(r.get("url") for r in (rows or []) if r.get("url")))
        except Exception as e:
            logger.debug(f"[STAGE2] No prior failure history ({e}); retry counts start at 0")
            return {}

    def _send_to_dlq(self, rows: list[dict[str, Any]]) -> None:
        """Escalate permanently failed URLs to the dead-letter queue (stage=stage2)."""
        try:
            if self._dlq is None:
                from src.utils.dead_letter_queue import DeadLetterQueue

                self._dlq = DeadLetterQueue()
            for row in rows:
                reason = row.get("error_message") or "stage2 analysis failed"
                self._dlq.add(
                    {"url": row.get("url"), "url_hash": row.get("url_hash"), "_retry_count": row.get("_retry_count", 0)},
                    RuntimeError(f"{reason} (error_code={row.get('error_code', 0)})"),
                    stage="stage2",
                    context={"max_retries": self.max_retries},
                )
        except Exception as e:
            logger.error(f"[STAGE2] Failed to write {len(rows)} entries to DLQ: {e}")

    async def run(self) -> dict[str, int]:
        """Analyze pending URLs; returns this run's counts (analyzed/quality_docs/massive_docs/errors)."""
        init_tracing(service_name="stage2-worker")
        crawl_job_id = ensure_crawl_job_id()
        with start_span("stage2.run", stage="stage2", crawl_job_id=crawl_job_id):
            return await self._run_traced()

    async def _run_traced(self) -> dict[str, int]:
        counts = {"analyzed": 0, "quality_docs": 0, "massive_docs": 0, "errors": 0}
        logger.info(f"[STAGE2] Worker starting with {self.max_concurrent} concurrent workers")

        try:
            queue_data = self.delta.read_table("stage2_queue")
            # LakehouseManager / DeltaHelper return list[dict]; tolerate pyarrow Table
            if hasattr(queue_data, "to_pylist"):
                all_queue_items = queue_data.to_pylist()
            else:
                all_queue_items = queue_data or []
        except Exception as e:
            logger.warning(f"[STAGE2] No URLs found in stage2_queue: {e}")
            return counts

        if not all_queue_items:
            logger.warning("[STAGE2] No URLs found in stage2_queue")
            return counts

        pending = [item for item in all_queue_items if item.get("status") == "pending"]

        logger.info(f"[STAGE2] Found {len(pending)} pending URLs to analyze (out of {len(all_queue_items)} total)")

        if not pending:
            logger.info("[STAGE2] No pending URLs to process")
            return counts

        prior_failures = self._load_prior_failures()

        for i in range(0, len(pending), self.batch_size):
            batch = pending[i : i + self.batch_size]
            logger.info(f"Processing batch {i // self.batch_size + 1}: {len(batch)} URLs")

            batch_start = time.time()

            tasks = [self._analyze_url(record) for record in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            batch_time = time.time() - batch_start

            valid_results = self._normalize_gather_results(batch, results)
            # Silver analysis excludes failures; they go to a quarantine table (#331).
            accepted, quarantined = split_stage2_results(valid_results)

            if accepted:
                self.delta.write(
                    "stage2_page_analysis",
                    accepted,
                    mode="append",
                    async_write=False,
                )
                logger.info(f"[STAGE2] Saved {len(accepted)} analysis results")
            if quarantined:
                self.delta.write(
                    TABLE_STAGE2_ERRORS,
                    quarantined,
                    mode="append",
                    async_write=False,
                )
                logger.info(f"[STAGE2] Quarantined {len(quarantined)} failed URLs to {TABLE_STAGE2_ERRORS}")
            if STAGE2_ROWS is not None:
                STAGE2_ROWS.labels(outcome="accepted").inc(len(accepted))
                STAGE2_ROWS.labels(outcome="quarantined").inc(len(quarantined))

            for r in valid_results:
                counts["analyzed"] += 1
                if r.get("has_error"):
                    counts["errors"] += 1
                elif r.get("is_massive_doc"):
                    counts["massive_docs"] += 1
                elif not r.get("is_low_quality", True):
                    counts["quality_docs"] += 1

            # Only successes complete; errors retry until capped, then fail + DLQ (#160).
            completed_urls, failed_urls, retrying = plan_queue_updates(
                valid_results, prior_failures, self.max_retries
            )
            if completed_urls:
                await self._update_queue_status(completed_urls)
            if failed_urls:
                await self._update_queue_status(failed_urls, status="failed")
                failed_set = set(failed_urls)
                self._send_to_dlq([r for r in valid_results if r.get("url") in failed_set])
            if retrying:
                logger.info(f"[STAGE2] {len(retrying)} failed URLs left pending for retry")

            if self.postgres and len(valid_results) > 0:
                try:
                    self.postgres.log_performance_metric(
                        stage="stage2",
                        urls_processed=len(valid_results),
                        processing_time_seconds=batch_time,
                        worker_count=self.max_concurrent,
                    )
                except Exception as e:
                    logger.debug(f"Failed to log performance to PostgreSQL: {e}")

        logger.info("[STAGE2] Worker completed all batches")
        return counts

    def _normalize_gather_results(
        self, batch: list[dict[str, Any]], results: list[Any]
    ) -> list[dict[str, Any]]:
        """Turn exceptions from ``gather`` into error records instead of dropping them (#214).

        An exception result becomes a normal ``has_error`` row for its input URL, so
        it is quarantined to stage2_errors and follows the retry/DLQ policy (#160)
        like any other failure. ``results`` is in ``batch`` order (``gather`` keeps it).
        """
        rows: list[dict[str, Any]] = []
        for record, result in zip(batch, results, strict=False):
            if isinstance(result, dict):
                rows.append(result)
                continue
            url = record.get("url") if isinstance(record.get("url"), str) else ""
            url_hash = record.get("url_hash") if isinstance(record.get("url_hash"), str) else ""
            if isinstance(result, BaseException):
                name = type(result).__name__
                logger.error(f"[STAGE2] Unhandled {name} analysing {str(url)[:80]}: {result}", exc_info=result)
                message = f"exception: {name}: {result}"
            else:
                name = type(result).__name__
                logger.error(f"[STAGE2] Unexpected {name} result for {str(url)[:80]}; recording as error")
                message = f"unexpected_result: {name}"
            if STAGE2_GATHER_EXCEPTIONS is not None:
                STAGE2_GATHER_EXCEPTIONS.labels(exception=name).inc()
            rows.append(self._error_record(url or "", url_hash or "", 0, message[:500]))
        return rows

    async def _update_queue_status(
        self, completed_urls: list[str], table_name: str = "stage2_queue", status: str = "completed"
    ):
        """Set ``status`` (and ``completed_at`` as the finish time) for the given URLs."""
        if not completed_urls:
            return

        try:
            update_data = {
                "url": pa.array(completed_urls, type=pa.string()),
                "status": pa.array([status] * len(completed_urls), type=pa.string()),
                "completed_at": pa.array(
                    [datetime.now() for _ in completed_urls],
                    type=pa.timestamp("ms"),
                ),
            }
            updates_table = pa.Table.from_pydict(update_data)

            target_table = DeltaTable(self.delta.get_table_path(table_name))

            (
                target_table.merge(
                    source=updates_table,
                    predicate="target.url = source.url",
                    source_alias="source",
                    target_alias="target",
                )
                .when_matched_update(
                    updates={
                        "status": "source.status",
                        "completed_at": "source.completed_at",
                    }
                )
                .execute()
            )

            logger.info(f"[STAGE2] Marked {len(completed_urls)} items as {status} in {table_name} via MERGE")

        except Exception as e:
            logger.error(f"[STAGE2] Failed to update queue status via MERGE: {e}")
            logger.info("[STAGE2] Falling back to overwrite method for this batch")
            try:
                all_items = self.delta.read(table_name)
                self._update_queue_status_overwrite(all_items, completed_urls, table_name, status=status)
            except Exception as fallback_e:
                logger.error(f"[STAGE2] Fallback overwrite method also failed: {fallback_e}")

    def _update_queue_status_overwrite(
        self, all_queue_items: list, completed_urls: list, table_name: str = "stage2_queue", status: str = "completed"
    ):
        """DEPRECATED: Original method to update queue status by overwriting the table."""
        try:
            completed_set = set(completed_urls)

            for item in all_queue_items:
                if item.get("url") in completed_set:
                    item["status"] = status
                    item["completed_at"] = datetime.now().isoformat()

            self.delta.write(table_name, all_queue_items, mode="overwrite", async_write=False)
            logger.info(f"[STAGE2] Marked {len(completed_urls)} items as {status} in {table_name} (overwrite)")

        except Exception as e:
            logger.error(f"[STAGE2] Failed to update queue status (overwrite): {e}")

    async def _analyze_url(self, record: dict[str, Any]) -> dict[str, Any]:
        url_value = record.get("url")
        url_hash_value = record.get("url_hash")

        if not isinstance(url_value, str) or not url_value:
            fallback_url = "" if url_value is None else str(url_value)
            fallback_hash = url_hash_value if isinstance(url_hash_value, str) else ""
            return self._error_record(fallback_url, fallback_hash, 0, "invalid_url")

        url = url_value
        url_hash = url_hash_value if isinstance(url_hash_value, str) else ""
        is_heavy = bool(record.get("is_heavy", False))

        async with self.semaphore:
            try:
                timeout = aiohttp.ClientTimeout(total=30)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url, allow_redirects=True) as response:
                        if response.status >= 400:
                            return self._error_record(url, url_hash, response.status, "http_error")

                        content_type = response.headers.get("Content-Type", "").lower()

                        if "text/html" in content_type:
                            html = await response.text()
                            return await self._analyze_html(url, url_hash, html, is_heavy)
                        elif "application/pdf" in content_type:
                            return self._route_pdf_to_stage4(url, url_hash)
                        else:
                            return self._minimal_record(url, url_hash, content_type)

            except TimeoutError as e:
                self._log_error_to_postgres(url, "TimeoutError", str(e))
                return self._error_record(url, url_hash, 0, "timeout")
            except aiohttp.ClientError as e:
                error_type = f"ClientError: {type(e).__name__}"
                self._log_error_to_postgres(url, error_type, str(e))
                return self._error_record(url, url_hash, 0, error_type)
            except Exception as e:
                logger.error(f"Failed to analyze {url}: {e}")
                self._log_error_to_postgres(url, type(e).__name__, str(e))
                return self._error_record(url, url_hash, 0, f"error: {str(e)}")

    async def _analyze_html(self, url: str, url_hash: str, html: str, is_heavy: bool) -> dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")

        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else "Untitled"

        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "iframe"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)
        text = " ".join(text.split())

        word_count = len(text.split())
        content_length = len(text)
        html_length = len(html)
        text_to_html_ratio = content_length / html_length if html_length > 0 else 0

        is_low_quality = word_count < self.MIN_WORD_COUNT or text_to_html_ratio < self.MIN_TEXT_TO_HTML_RATIO

        is_massive_doc = content_length > self.MASSIVE_DOC_THRESHOLD

        if is_massive_doc and not is_low_quality:
            await self._route_to_stage4(url, url_hash, text, word_count, content_length)
            logger.info(f"Routed large doc ({content_length} chars) to Stage 4: {url[:80]}")

        keywords = []
        if not is_low_quality and not is_massive_doc:
            keywords = await self._extract_keywords_async(text, is_heavy)

        quality_score = self._calculate_quality_score(word_count, text_to_html_ratio)

        return {
            "url": url or "",
            "url_hash": url_hash or "",
            "title": title or "",
            "word_count": word_count or 0,
            "content_length": content_length or 0,
            "html_length": html_length or 0,
            "text_to_html_ratio": (round(text_to_html_ratio, 3) if text_to_html_ratio else 0.0),
            "is_low_quality": is_low_quality if is_low_quality is not None else True,
            "is_massive_doc": is_massive_doc if is_massive_doc is not None else False,
            "quality_score": quality_score if quality_score is not None else 0.0,
            "text_content": text[:10000] if (not is_low_quality and text) else "",
            "keywords": (keywords if keywords else [""]),
            "has_error": False,
            "processed_at": datetime.now().isoformat(),
        }

    async def _extract_keywords_async(self, text: str, is_heavy: bool) -> list[str]:
        if not text or len(text) < 50:
            return []

        try:
            loop = asyncio.get_running_loop()
            keywords = await loop.run_in_executor(None, self._extract_keywords_sync, text, is_heavy)
            return keywords
        except Exception as e:
            logger.warning(f"YAKE extraction failed: {e}")
            return []

    def _extract_keywords_sync(self, text: str, is_heavy: bool) -> list[str]:
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

        except ImportError:
            logger.warning("YAKE not installed")
            return []
        except Exception as e:
            logger.warning(f"YAKE failed: {e}")
            return []

    def _calculate_quality_score(self, word_count: int, text_ratio: float) -> float:
        word_score = min(word_count / 1000, 0.6)
        ratio_score = min(text_ratio * 0.4, 0.4)
        return round(word_score + ratio_score, 3)

    async def _route_to_stage4(self, url: str, url_hash: str, text: str, word_count: int, content_length: int):
        record = {
            "url": url,
            "url_hash": url_hash,
            "word_count": word_count,
            "content_length": content_length,
            "status": "pending",
            "queued_at": datetime.now().isoformat(),
        }

        try:
            self.delta.write("stage4_large_docs", [record], mode="append", async_write=True)
        except Exception as e:
            logger.error(f"Failed to route to Stage 4: {e}")

    def _route_pdf_to_stage4(self, url: str, url_hash: str) -> dict[str, Any]:
        record = {
            "url": url,
            "url_hash": url_hash,
            "word_count": 0,
            "content_length": 0,
            "status": "pending",
            "is_pdf": True,
            "queued_at": datetime.now().isoformat(),
        }

        self.delta.write("stage4_large_docs", [record], mode="append", async_write=True)

        return {
            "url": url or "",
            "url_hash": url_hash or "",
            "title": "PDF Document",
            "word_count": 0,
            "content_length": 0,
            "html_length": 0,
            "text_to_html_ratio": 0.0,
            "is_low_quality": True,
            "is_massive_doc": False,
            "quality_score": 0.0,
            "text_content": "",
            "keywords": [""],
            "is_pdf": True,
            "routed_to_stage4": True,
            "has_error": False,
            "processed_at": datetime.now().isoformat(),
        }

    def _minimal_record(self, url: str, url_hash: str, content_type: str) -> dict[str, Any]:
        return {
            "url": url or "",
            "url_hash": url_hash or "",
            "title": "Binary/Other Content",
            "content_type": content_type or "",
            "word_count": 0,
            "content_length": 0,
            "html_length": 0,
            "text_to_html_ratio": 0.0,
            "is_low_quality": True,
            "is_massive_doc": False,
            "quality_score": 0.0,
            "text_content": "",
            "keywords": [""],
            "has_error": False,
            "processed_at": datetime.now().isoformat(),
        }

    def _error_record(self, url: str, url_hash: str, error_code: int, error_msg: str) -> dict[str, Any]:
        return {
            "url": url or "",
            "url_hash": url_hash or "",
            "title": "Error",
            "has_error": True,
            "error_code": error_code or 0,
            "error_message": error_msg or "",
            "word_count": 0,
            "content_length": 0,
            "html_length": 0,
            "text_to_html_ratio": 0.0,
            "is_low_quality": True,
            "is_massive_doc": False,
            "quality_score": 0.0,
            "text_content": "",
            "keywords": [""],
            "processed_at": datetime.now().isoformat(),
        }

    def _log_error_to_postgres(
        self,
        url: str,
        error_type: str,
        error_message: str,
        http_status: int | None = None,
    ):
        """Helper to log errors to PostgreSQL."""
        if self.postgres:
            try:
                self.postgres.log_error(
                    stage="stage2",
                    url=url,
                    error_type=error_type,
                    error_message=error_message,
                    http_status_code=http_status,
                )
            except Exception as e:
                logger.debug(f"Failed to log error to PostgreSQL: {e}")

async def run_stage2_worker():
    logger.info("Stage 2 Worker starting in continuous mode...")

    max_concurrent, batch_size = stage_worker_settings(2, 50, 100)
    logger.info("Stage 2 Worker concurrency=%d batch_size=%d", max_concurrent, batch_size)

    while True:
        try:
            worker = Stage2Worker(max_concurrent=max_concurrent, batch_size=batch_size)
            await worker.run()
            logger.info("Waiting 30 seconds before next check...")
            await asyncio.sleep(30)
        except KeyboardInterrupt:
            logger.info("Stage 2 Worker shutting down...")
            break
        except Exception as e:
            logger.error(f"Error in Stage 2 Worker loop: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(run_stage2_worker())
