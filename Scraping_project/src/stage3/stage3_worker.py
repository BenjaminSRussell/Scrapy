import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from datasketch import MinHash, MinHashLSH

from src.core.config import get_config, stage_worker_settings
from src.core.constants import (
    LEGACY_TABLE_STAGE3_SUMMARIES,
    SUMMARY_LIMITS,
    TABLE_STAGE3_SUMMARIES,
)
from src.otel_tracing import ensure_crawl_job_id, init_tracing, start_span
from src.utils.delta import get_delta
from src.utils.metrics_sink import record_error, record_performance
from src.utils.postgres import PostgresManager

logger = logging.getLogger(__name__)

DEFAULT_SIMILARITY_THRESHOLD = 0.3
# The extractive summary keeps the first N sentences, but text without sentence
# punctuation would otherwise come back whole as its own "summary" (#223).
MAX_SUMMARY_CHARS = 1000


def _similarity_threshold() -> float:
    """``stage3.similarity_threshold`` from config.yml (MinHash LSH Jaccard, 0 < t <= 1).

    The worker used a hard-coded 0.3 and ignored the documented setting.
    """
    try:
        value = float(get_config().get("stage3.similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD))
    except (TypeError, ValueError):
        return DEFAULT_SIMILARITY_THRESHOLD
    if not 0.0 < value <= 1.0:
        logger.warning(f"stage3.similarity_threshold={value} outside (0, 1]; using {DEFAULT_SIMILARITY_THRESHOLD}")
        return DEFAULT_SIMILARITY_THRESHOLD
    return value


class Stage3Worker:

    def __init__(self, max_concurrent: int = 20, batch_size: int = 50):
        self.max_concurrent = max_concurrent
        self.batch_size = batch_size
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.delta = get_delta()
        self.postgres = PostgresManager.get_instance()
        self.SIMILARITY_THRESHOLD = _similarity_threshold()

    def _processed_hashes(self) -> set:
        """url_hashes Stage 3 has already summarized (#316/#612).

        Stage 3's output table is TABLE_STAGE3_SUMMARIES. Lakes written before
        #612 hold Stage 3 rows under LEGACY_TABLE_STAGE3_SUMMARIES, so both are
        read: dropping the legacy read would re-summarize every old document.
        Each table is optional; a missing one contributes nothing.
        """
        hashes: set = set()
        for table in (TABLE_STAGE3_SUMMARIES, LEGACY_TABLE_STAGE3_SUMMARIES):
            try:
                rows = self.delta.read(table) or []
            except Exception:
                continue
            hashes.update(r["url_hash"] for r in rows if r.get("url_hash"))
        return hashes

    async def run(self) -> int:
        """Summarize pending quality docs; returns summaries written this run."""
        init_tracing(service_name="stage3-worker")
        crawl_job_id = ensure_crawl_job_id()
        with start_span("stage3.run", stage="stage3", crawl_job_id=crawl_job_id):
            return await self._run_traced()

    async def _run_traced(self) -> int:
        written = 0
        logger.info(f"Stage 3 Worker starting with {self.max_concurrent} concurrent workers")

        all_docs = self.delta.read("stage2_page_analysis")

        if not all_docs:
            logger.warning("No documents found in stage2_page_analysis")
            return written

        quality_docs = [
            doc
            for doc in all_docs
            if not doc.get("is_low_quality", True)
            and not doc.get("is_massive_doc", False)
            and not doc.get("has_error", False)
            and doc.get("text_content")
        ]

        logger.info(f"Found {len(quality_docs)} quality documents to process")

        if not quality_docs:
            logger.info("No quality documents to process")
            return written

        processed_hashes = self._processed_hashes()

        pending = [doc for doc in quality_docs if doc.get("url_hash") not in processed_hashes]

        if not pending:
            logger.info("All quality documents already processed")
            return written

        logger.info(f"Processing {len(pending)} pending documents")

        for i in range(0, len(pending), self.batch_size):
            batch = pending[i : i + self.batch_size]
            logger.info(f"Processing batch {i // self.batch_size + 1}: {len(batch)} documents")

            batch_start = time.time()

            unique_batch = await self._deduplicate_documents(batch)
            logger.info(f"After deduplication: {len(unique_batch)} unique documents")

            tasks = [self._summarize_document(doc) for doc in unique_batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            batch_time = time.time() - batch_start

            valid_results = [r for r in results if isinstance(r, dict) and not isinstance(r, Exception)]

            if valid_results:
                self.delta.write(TABLE_STAGE3_SUMMARIES, valid_results, mode="append", async_write=False)
                written += len(valid_results)
                logger.info(f"Saved {len(valid_results)} summaries")

                # #586: dual-export; never raises, never silent on a sink failure.
                record_performance(
                    self.postgres,
                    stage="stage3",
                    urls_processed=len(valid_results),
                    processing_time_seconds=batch_time,
                    worker_count=self.max_concurrent,
                )

            if _drain_requested(min(i + self.batch_size, len(pending)), len(pending)):
                break

        logger.info("Stage 3 Worker completed all batches")
        return written

    async def _deduplicate_documents(self, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        logger.info(f"Running similarity detection on {len(documents)} documents")

        lsh = MinHashLSH(threshold=self.SIMILARITY_THRESHOLD, num_perm=128)

        unique_docs = []
        seen_similar = set()

        for doc in documents:
            url_hash = doc.get("url_hash")
            text = doc.get("text_content", "")

            if not text or url_hash in seen_similar:
                continue

            minhash = MinHash(num_perm=128)

            words = text.lower().split()
            for word in words[:1000]:
                minhash.update(word.encode("utf-8"))

            similar = lsh.query(minhash)

            if similar or url_hash in seen_similar:
                logger.debug(f"Skipping duplicate: {doc.get('url', '')[:80]}")
                seen_similar.add(url_hash)
                continue

            try:
                lsh.insert(url_hash, minhash)
                seen_similar.add(url_hash)
                unique_docs.append(doc)
            except ValueError:
                logger.debug(f"Key already exists in LSH: {doc.get('url', '')[:80]}")
                continue

        logger.info(f"Deduplication: {len(unique_docs)} unique out of {len(documents)}")
        return unique_docs

    async def _summarize_document(self, doc: dict[str, Any]) -> dict[str, Any] | None:
        async with self.semaphore:
            try:
                url = doc.get("url", "")
                text = doc.get("text_content", "")
                url_hash = doc.get("url_hash", "")

                max_sentences = SUMMARY_LIMITS["extractive_max_sentences"]
                sentences = text.split(".")[:max_sentences]
                summary_body = ". ".join(sentence.strip() for sentence in sentences if sentence.strip())
                summary = summary_body + "." if summary_body else ""
                if len(summary) > MAX_SUMMARY_CHARS:
                    summary = self._fallback_summary(summary, MAX_SUMMARY_CHARS)

                return {
                    "url": url,
                    "url_hash": url_hash,
                    "summary": summary,
                    "word_count": len(text.split()),
                    "keywords": doc.get("keywords", []),
                    "quality_score": doc.get("quality_score", 0),
                    "timestamp": datetime.now().isoformat(),
                }

            except Exception as err:
                logger.error(f"Summarization failed for {doc.get('url', '')}: {err}")

                record_error(
                    self.postgres,
                    stage="stage3",
                    url=doc.get("url", ""),
                    error_type=type(err).__name__,
                    error_message=str(err),
                )

                return None

    def _fallback_summary(self, text: str, max_chars: int = 500) -> str:
        """At most ``max_chars``; ends at a sentence when possible (#740)."""
        from src.utils.text_truncate import truncate_text

        return truncate_text(text, max_chars)

    def _extract_key_facts(self, text: str, keywords: list[str]) -> list[str]:
        sentences = text.split(".")
        facts = []

        for sentence in sentences[:20]:
            sentence = sentence.strip()
            if not sentence:
                continue

            for keyword in keywords:
                if keyword.lower() in sentence.lower():
                    facts.append(sentence)
                    break

            if len(facts) >= 5:
                break

        return facts if facts else [s.strip() for s in sentences[:3] if s.strip()]

def _drain_requested(done: int, total: int) -> bool:
    """SIGTERM/SIGINT seen: stop after the batch just written (#325)."""
    from src.utils.graceful_shutdown import shutdown_requested

    if not shutdown_requested():
        return False
    logger.info(f"[STAGE3] Shutdown requested: stopping after {done}/{total}; the rest is picked up next run")
    return True


async def run_stage3_worker(shutdown=None):
    from src.utils.graceful_shutdown import run_drain_loop

    logger.info("Stage 3 Worker starting in continuous mode...")

    max_concurrent, batch_size = stage_worker_settings(3, 20, 50)
    logger.info("Stage 3 Worker concurrency=%d batch_size=%d", max_concurrent, batch_size)

    async def run_once():
        await Stage3Worker(max_concurrent=max_concurrent, batch_size=batch_size).run()

    await run_drain_loop("stage3", run_once, idle_seconds=30, error_seconds=10, shutdown=shutdown)

if __name__ == "__main__":
    from src.utils.worker_metrics import start_worker_metrics_server

    start_worker_metrics_server("stage3")  # #789
    asyncio.run(run_stage3_worker())
