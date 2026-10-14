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
from src.utils.cost_controls import (
    GPU_MINUTES,
    STATUS_BUDGET_EXHAUSTED,
    ensure_budget,
    has_numeric_cap,
    stop_for_budget,
)
from src.utils.delta import get_delta
from src.utils.metrics_sink import record_error, record_performance
from src.utils.optional_deps import require_stage3_deps
from src.utils.postgres import PostgresManager

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter

    STAGE3_DOCUMENTS_DEDUPLICATED: Any = Counter(
        "stage3_documents_deduplicated_total",
        "Documents skipped by Stage 3 MinHash dedup, including matches from earlier batches (#222).",
    )
except Exception:  # prometheus_client missing or metric already registered
    STAGE3_DOCUMENTS_DEDUPLICATED = None

DEFAULT_SIMILARITY_THRESHOLD = 0.3
_NUM_PERM = 128
# Redis HASH: url_hash -> MinHash.hashvalues. No TTL, so volatile-lru will not
# evict it and a later process can rebuild the LSH (#222).
_FINGERPRINT_KEY = "stage3:minhash:fingerprints"
_fingerprint_redis: Any = None
_fingerprint_redis_warned = False
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


def _note_deduplicated(count: int) -> None:
    if count > 0 and STAGE3_DOCUMENTS_DEDUPLICATED is not None:
        STAGE3_DOCUMENTS_DEDUPLICATED.inc(count)


def _minhash_for_text(text: str) -> MinHash:
    sketch = MinHash(num_perm=_NUM_PERM)
    for word in text.lower().split()[:1000]:
        sketch.update(word.encode("utf-8"))
    return sketch


def _encode_hashvalues(sketch: MinHash) -> str:
    return ",".join(str(int(value)) for value in sketch.hashvalues)


def _minhash_from_payload(payload: Any) -> MinHash:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    values = [int(part) for part in str(payload).split(",") if part]
    if len(values) != _NUM_PERM:
        raise ValueError(f"expected {_NUM_PERM} hash values, got {len(values)}")
    return MinHash(num_perm=_NUM_PERM, hashvalues=values)


def _as_key(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _fingerprint_redis_client() -> Any:
    """Short-timeout Redis client, or None when Redis is down.

    Not the shared RedisHelper: that helper waits 5s and then keeps the dead
    client, which would stall every Stage 3 batch on a machine without Redis.
    """
    global _fingerprint_redis, _fingerprint_redis_warned
    if _fingerprint_redis is not None:
        try:
            _fingerprint_redis.ping()
            return _fingerprint_redis
        except Exception:
            _fingerprint_redis = None
    try:
        import socket

        import redis

        from src.utils.redis_env import redis_settings

        env = redis_settings()
        # redis-py retries a refused connection for several seconds. Probe first
        # so a worker on a machine without Redis does not stall the batch.
        with socket.create_connection((env.host, int(env.port)), timeout=0.25):
            pass
        client = redis.Redis(
            host=env.host,
            port=env.port,
            db=env.db,
            password=env.password or None,
            decode_responses=True,
            socket_connect_timeout=0.25,
            socket_timeout=0.5,
        )
        client.ping()
    except Exception as exc:
        if not _fingerprint_redis_warned:
            logger.warning("Stage 3 MinHash store is in-memory only (Redis unavailable): %s", exc)
            _fingerprint_redis_warned = True
        else:
            logger.debug("Stage 3 MinHash Redis still unavailable: %s", exc)
        return None
    _fingerprint_redis = client
    _fingerprint_redis_warned = False
    return client


class FingerprintStore:
    """MinHash LSH plus an optional Redis hash (#222).

    The LSH lives for the worker process, so a later batch sees earlier
    fingerprints even when Redis is down. Redis is what makes them survive a
    new process.
    """

    def __init__(self, threshold: float, redis_client: Any = None) -> None:
        self._lsh = MinHashLSH(threshold=threshold, num_perm=_NUM_PERM)
        self._redis = redis_client
        self._load()

    def is_similar(self, sketch: MinHash) -> bool:
        return bool(self._lsh.query(sketch))

    def add(self, key: Any, sketch: MinHash) -> bool:
        """Insert ``key``. False when that key is already stored."""
        try:
            self._lsh.insert(key, sketch)
        except ValueError:
            return False
        self._persist(key, sketch)
        return True

    def _load(self) -> None:
        if self._redis is None:
            return
        try:
            raw = self._redis.hgetall(_FINGERPRINT_KEY) or {}
        except Exception as exc:
            logger.warning("Stage 3 fingerprint load failed (%s); dedup is in-memory for this process", exc)
            self._redis = None
            return
        bad = 0
        for key, payload in raw.items():
            try:
                self._lsh.insert(_as_key(key), _minhash_from_payload(payload))
            except (ValueError, TypeError):
                bad += 1
        if bad:
            logger.warning("Stage 3 ignored %d unreadable MinHash fingerprints in Redis", bad)

    def _persist(self, key: Any, sketch: MinHash) -> None:
        if self._redis is None or not isinstance(key, str) or not key:
            return
        try:
            self._redis.hset(_FINGERPRINT_KEY, mapping={key: _encode_hashvalues(sketch)})
        except Exception as exc:
            logger.warning("Stage 3 fingerprint persist failed for %s: %s", key, exc)
            self._redis = None


class Stage3Worker:

    def __init__(self, max_concurrent: int = 20, batch_size: int = 50):
        require_stage3_deps()  # datasketch/core only, not torch (#144)
        self.max_concurrent = max_concurrent
        self.batch_size = batch_size
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.delta = get_delta()
        self.postgres = PostgresManager.get_instance()
        self.SIMILARITY_THRESHOLD = _similarity_threshold()
        self._fingerprints: FingerprintStore | None = None
        self.budget_status: str | None = None

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
        self.budget_status = None
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
            # #528: missing/null caps fall through. A spent numeric cap stops here,
            # before dedup (Redis) and before summarization (GPU-shaped work).
            if stop_for_budget(self, "stage3"):
                break
            batch = pending[i : i + self.batch_size]
            logger.info(f"Processing batch {i // self.batch_size + 1}: {len(batch)} documents")

            batch_start = time.time()

            if has_numeric_cap(ensure_budget(self), "stage3"):
                # One document at a time. Dedup runs only after the cap check so a
                # document we cannot afford is not stored as a fingerprint (#222).
                results = []
                for doc in batch:
                    if stop_for_budget(self, "stage3"):
                        break
                    unique = await self._deduplicate_documents([doc])
                    if not unique:
                        continue
                    started = ensure_budget(self).now()
                    results.append(await self._summarize_document(unique[0]))
                    elapsed_min = (ensure_budget(self).now() - started).total_seconds() / 60.0
                    ensure_budget(self).charge("stage3", GPU_MINUTES, elapsed_min)
            else:
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
            if self.budget_status == STATUS_BUDGET_EXHAUSTED:
                break

        logger.info("Stage 3 Worker completed all batches")
        return written

    def _fingerprint_store(self) -> FingerprintStore:
        # __new__ test doubles never run Stage3Worker.__init__.
        # getattr is Any; annotate so mypy's warn_return_any stays clean.
        store: FingerprintStore | None = getattr(self, "_fingerprints", None)
        if store is None:
            store = FingerprintStore(self.SIMILARITY_THRESHOLD, redis_client=_fingerprint_redis_client())
            self._fingerprints = store
        return store

    async def _deduplicate_documents(self, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        logger.info(f"Running similarity detection on {len(documents)} documents")

        store = self._fingerprint_store()
        unique_docs = []
        seen_similar = set()
        skipped = 0

        for doc in documents:
            url_hash = doc.get("url_hash")
            text = doc.get("text_content", "")

            if not text:
                continue
            if url_hash in seen_similar:
                skipped += 1
                continue

            minhash = _minhash_for_text(text)
            # First copy wins. The store already holds fingerprints from earlier
            # batches and, when Redis is up, from previous processes (#222).
            if store.is_similar(minhash) or not store.add(url_hash, minhash):
                logger.debug(f"Skipping duplicate: {doc.get('url', '')[:80]}")
                seen_similar.add(url_hash)
                skipped += 1
                continue

            seen_similar.add(url_hash)
            unique_docs.append(doc)

        _note_deduplicated(skipped)
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
