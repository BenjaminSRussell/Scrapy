import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from datasketch import MinHash, MinHashLSH  # type: ignore[import-untyped]

from src.common.constants import SUMMARY_LIMITS
from src.common.delta_lake import get_delta_manager
from src.common.postgres_manager import PostgresManager

logger = logging.getLogger(__name__)

class Stage3Worker:

    def __init__(self, max_concurrent: int = 20, batch_size: int = 50):
        self.max_concurrent = max_concurrent
        self.batch_size = batch_size
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.delta = get_delta_manager()
        self.postgres = PostgresManager.get_instance()
        self.SIMILARITY_THRESHOLD = 0.3

    async def run(self):
        logger.info(f"Stage 3 Worker starting with {self.max_concurrent} concurrent workers")

        all_docs = self.delta.read("stage2_page_analysis")

        if not all_docs:
            logger.warning("No documents found in stage2_page_analysis")
            return

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
            return

        try:
            processed = self.delta.read("stage4_summaries")
            processed_hashes = {r["url_hash"] for r in processed}
        except Exception:
            processed_hashes = set()

        pending = [doc for doc in quality_docs if doc.get("url_hash") not in processed_hashes]

        if not pending:
            logger.info("All quality documents already processed")
            return

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
                self.delta.write("stage4_summaries", valid_results, mode="append", async_write=False)
                logger.info(f"Saved {len(valid_results)} summaries")

                if self.postgres:
                    try:
                        self.postgres.log_performance_metric(
                            stage="stage3",
                            urls_processed=len(valid_results),
                            processing_time_seconds=batch_time,
                            worker_count=self.max_concurrent,
                        )
                    except Exception as e:
                        logger.debug(f"Failed to log performance to PostgreSQL: {e}")

        logger.info("Stage 3 Worker completed all batches")

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

                if self.postgres:
                    try:
                        self.postgres.log_error(
                            stage="stage3",
                            url=doc.get("url", ""),
                            error_type=type(err).__name__,
                            error_message=str(err),
                        )
                    except Exception as pg_error:
                        logger.debug(f"Failed to log error to PostgreSQL: {pg_error}")

                return None

    def _fallback_summary(self, text: str, max_chars: int = 500) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "..."

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

async def run_stage3_worker():
    logger.info("Stage 3 Worker starting in continuous mode...")

    while True:
        try:
            worker = Stage3Worker(max_concurrent=20, batch_size=50)
            await worker.run()
            logger.info("Waiting 30 seconds before next check...")
            await asyncio.sleep(30)
        except KeyboardInterrupt:
            logger.info("Stage 3 Worker shutting down...")
            break
        except Exception as e:
            logger.error(f"Error in Stage 3 Worker loop: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(run_stage3_worker())
