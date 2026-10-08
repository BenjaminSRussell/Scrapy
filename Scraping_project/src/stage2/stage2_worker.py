import asyncio
import logging
import os
import random
import time
from collections import Counter as TallyCounter
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import aiohttp
import pyarrow as pa
from bs4 import BeautifulSoup
from deltalake import DeltaTable

from src.core.config import get_config, stage2_quality_thresholds, stage_worker_settings
from src.core.constants import TABLE_STAGE2_ERRORS
from src.utils.delta import get_delta
from src.utils.ssrf import SSRFBlocked, count_blocked, ssrf_block_reason
from src.utils.soft_ban import DomainBackoff, SoftBanDetector, count_deferred, count_soft_ban, domain_of
from src.utils.postgres import get_postgres_manager
from src.utils.retry import CircuitBreaker
from src.utils.metrics_sink import record_error, record_performance
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

try:  # queue status MERGE failures after retries (#168)
    from prometheus_client import Counter as _QCounter

    STAGE2_QUEUE_UPDATE_FAILURES = _QCounter(
        "stage2_queue_update_failures_total",
        "stage2_queue status MERGEs that failed after all retries (rows left pending).",
        ["status"],
    )
except Exception:
    STAGE2_QUEUE_UPDATE_FAILURES = None

try:  # analysis upsert failures; their queue rows are left pending (#311)
    from prometheus_client import Counter as _ACounter

    STAGE2_ANALYSIS_WRITE_FAILURES = _ACounter(
        "stage2_analysis_write_failures_total",
        "stage2_page_analysis upserts that failed (queue rows left pending, not acked).",
    )
except Exception:
    STAGE2_ANALYSIS_WRITE_FAILURES = None

try:  # per-host concurrency cap (#195)
    from prometheus_client import Counter as _HCounter

    STAGE2_HOST_THROTTLED = _HCounter(
        "stage2_host_throttled_total",
        "Stage 2 fetches that waited for a per-host concurrency slot (#195).",
    )
except Exception:
    STAGE2_HOST_THROTTLED = None

DEFAULT_STAGE2_PER_HOST_CONCURRENCY = 4


def stage2_per_host_concurrency(max_concurrent: int, config: Any = None) -> int:
    """Per-host in-flight cap for Stage 2 (#195), clamped to [1, max_concurrent].

    Precedence: env ``STAGE2_PER_HOST_CONCURRENCY`` > config
    ``stage2.per_host_concurrency`` > ``stages.stage2.per_host_concurrency`` > 4.
    """
    candidates: list[Any] = [os.getenv("STAGE2_PER_HOST_CONCURRENCY")]
    try:
        cfg = config if config is not None else get_config()
        candidates += [cfg.get("stage2.per_host_concurrency"), cfg.get("stages.stage2.per_host_concurrency")]
    except Exception:
        pass
    value = DEFAULT_STAGE2_PER_HOST_CONCURRENCY
    for raw in candidates:
        if raw in (None, ""):
            continue
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            logger.warning(f"[STAGE2] Ignoring invalid per-host concurrency {raw!r}")
            continue
        if parsed >= 1:
            value = parsed
            break
    return max(1, min(value, max(1, int(max_concurrent))))


try:  # in-request HTTP retries and per-host circuit breaker (#158)
    from prometheus_client import Counter as _HCounter

    STAGE2_HTTP_FETCHES = _HCounter(
        "stage2_http_fetches_total",
        "Stage 2 URL fetches by outcome: first_try, recovered (succeeded after a retry), "
        "exhausted (transient failure on every attempt) or circuit_open (host breaker open; URL deferred).",
        ["outcome"],
    )
    STAGE2_HTTP_RETRIES = _HCounter(
        "stage2_http_retries_total",
        "Stage 2 HTTP retry attempts by transient reason (timeout, connection, http_5xx/408).",
        ["reason"],
    )
except Exception:
    STAGE2_HTTP_FETCHES = None
    STAGE2_HTTP_RETRIES = None

DEFAULT_STAGE2_MERGE_RETRIES = 4
ANALYSIS_TABLE = "stage2_page_analysis"


def ensure_url_hash(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every analysis row a url_hash (the upsert key, #311) using the seed hasher."""
    from src.lakehouse.seed_manager import default_url_hasher

    for row in rows:
        if not row.get("url_hash"):
            row["url_hash"] = default_url_hasher(str(row.get("url") or ""))
    return rows


def split_stage2_results(results: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a batch into (accepted analysis rows, quarantined error rows) (#331)."""
    rows = [r for r in results if isinstance(r, dict)]
    accepted = [r for r in rows if not r.get("has_error")]
    quarantined = [r for r in rows if r.get("has_error")]
    return accepted, quarantined


DEFAULT_STAGE2_MAX_RETRIES = 3
SOFT_BAN_PREFIX = "soft_ban:"
# In-request retry policy (#158). 429 is deliberately absent: it is a soft-ban
# signal (#582) handled by quarantine + DomainBackoff, and retried by the queue.
TRANSIENT_HTTP_STATUSES = frozenset({408, 500, 502, 503, 504})
DEFAULT_STAGE2_HTTP_ATTEMPTS = 3
DEFAULT_STAGE2_HTTP_BACKOFF_BASE = 0.5
DEFAULT_STAGE2_HTTP_BACKOFF_MAX = 8.0
DEFAULT_STAGE2_BREAKER_FAILURES = 5
DEFAULT_STAGE2_BREAKER_RECOVERY = 60


def _env_number(name: str, default: float, minimum: float, cast: Any = float) -> Any:
    try:
        return max(minimum, cast(os.getenv(name, default)))
    except (TypeError, ValueError):
        return cast(default)


def _parse_retry_after(value: str | None) -> float | None:
    """Seconds from a numeric Retry-After header; HTTP-date forms are ignored."""
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        return None


class TransientHTTPError(Exception):
    """A retryable HTTP status (TRANSIENT_HTTP_STATUSES) that was not a soft ban."""

    def __init__(self, status: int, retry_after: float | None = None):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after


def _is_terminal_error(row: dict[str, Any]) -> bool:
    """Errors that retrying cannot fix: bad URLs and 4xx other than 408/429."""
    if row.get("error_message") == "invalid_url":
        return True
    if str(row.get("error_message") or "").startswith("ssrf_blocked:"):
        return True  # policy refusal (#682): retrying cannot make it safe
    if str(row.get("error_message") or "").startswith(SOFT_BAN_PREFIX):
        return False  # soft ban/captcha (#582): retry after backoff, up to max_retries
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

        # Quality gates from config.yml stage2.* / STAGE2_* env (#329).
        thresholds = stage2_quality_thresholds()
        self.MIN_WORD_COUNT = thresholds.min_word_count
        self.MIN_TEXT_TO_HTML_RATIO = thresholds.min_text_to_html_ratio
        self.MASSIVE_DOC_THRESHOLD = thresholds.massive_doc_threshold
        logger.info(
            f"[STAGE2] Quality thresholds: min_word_count={self.MIN_WORD_COUNT}, "
            f"min_text_to_html_ratio={self.MIN_TEXT_TO_HTML_RATIO}, "
            f"massive_doc_threshold={self.MASSIVE_DOC_THRESHOLD}"
        )

        self.perf_start_time = None
        self.perf_urls_processed = 0

        try:
            self.max_retries = max(1, int(os.getenv("STAGE2_MAX_RETRIES", DEFAULT_STAGE2_MAX_RETRIES)))
        except ValueError:
            self.max_retries = DEFAULT_STAGE2_MAX_RETRIES
        self._dlq: Any = None
        # One pooled HTTP session per run (#200); created in _run_traced.
        self._session: aiohttp.ClientSession | None = None
        # Soft-ban/captcha guard (#582).
        self.soft_ban = SoftBanDetector()
        self.domain_backoff = DomainBackoff(stage="stage2")
        # In-request retries + per-host circuit breaker (#158).
        self.http_attempts = _env_number("STAGE2_HTTP_ATTEMPTS", DEFAULT_STAGE2_HTTP_ATTEMPTS, 1, int)
        self.http_backoff_base = _env_number("STAGE2_HTTP_BACKOFF_BASE", DEFAULT_STAGE2_HTTP_BACKOFF_BASE, 0.0)
        self.http_backoff_max = _env_number("STAGE2_HTTP_BACKOFF_MAX", DEFAULT_STAGE2_HTTP_BACKOFF_MAX, 0.0)
        self.breaker_failures = _env_number("STAGE2_BREAKER_FAILURES", DEFAULT_STAGE2_BREAKER_FAILURES, 1, int)
        self.breaker_recovery = _env_number("STAGE2_BREAKER_RECOVERY", DEFAULT_STAGE2_BREAKER_RECOVERY, 0, int)
        self._host_breakers: dict[str, CircuitBreaker] = {}
        # Per-host concurrency cap (#195): a batch dominated by one host can't
        # stampede it or hog the global slots other hosts are waiting for.
        self.per_host_concurrency = stage2_per_host_concurrency(max_concurrent)
        self._host_slots: dict[str, asyncio.Semaphore] = {}
        logger.info(
            f"[STAGE2] Concurrency: global={max_concurrent}, per_host={self.per_host_concurrency}"
        )

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
            async with self._http_session():
                return await self._run_traced()

    def _per_host_limit(self) -> int:
        limit = getattr(self, "per_host_concurrency", None)
        if limit is None:  # instances built without __init__ (tests)
            limit = stage2_per_host_concurrency(self.max_concurrent)
            self.per_host_concurrency = limit
        return int(limit)

    def _host_slot(self, domain: str) -> asyncio.Semaphore:
        """The per-host semaphore for ``domain`` (created on first use)."""
        slots: dict[str, asyncio.Semaphore] | None = getattr(self, "_host_slots", None)
        if slots is None:
            slots = self._host_slots = {}
        slot: asyncio.Semaphore | None = slots.get(domain)
        if slot is None:
            slot = slots[domain] = asyncio.Semaphore(self._per_host_limit())
        return slot

    def _new_session(self) -> aiohttp.ClientSession:
        """Pooled session: connector limit matches worker concurrency (#200),
        and connections per host match the per-host cap (#195)."""
        connector = aiohttp.TCPConnector(
            limit=self.max_concurrent,
            limit_per_host=self._per_host_limit(),
            ttl_dns_cache=300,
        )
        return aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=30))

    @asynccontextmanager
    async def _http_session(self):
        """Share one ClientSession across every batch of a run; always closed."""
        session = self._new_session()
        self._session = session
        self._host_slots = {}  # semaphores bind to the running loop; fresh per run
        try:
            yield session
        finally:
            self._session = None
            await session.close()

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
            # Domain in soft-ban cooldown (#582): not attempted, not a failure,
            # stays pending for the next run.
            deferred = [r for r in valid_results if r.get("_deferred")]
            if deferred:
                valid_results = [r for r in valid_results if not r.get("_deferred")]
                logger.warning(f"[STAGE2] Deferred {len(deferred)} URLs from domains in soft-ban cooldown")
            # Silver analysis excludes failures; they go to a quarantine table (#331).
            accepted, quarantined = split_stage2_results(valid_results)

            # #311: upsert by url_hash, so reprocessing a URL whose queue ack was
            # lost (crash between write and ack) replaces its row instead of
            # duplicating it; and only ack URLs whose analysis is durable.
            analysis_ok = await self._write_analysis(accepted) if accepted else True
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
            if not analysis_ok:
                logger.error(
                    f"[STAGE2] Analysis upsert failed; leaving {len(completed_urls)} URLs pending (not acked)"
                )
                completed_urls = []
            if completed_urls:
                await self._update_queue_status(completed_urls)
            if failed_urls:
                await self._update_queue_status(failed_urls, status="failed")
                failed_set = set(failed_urls)
                self._send_to_dlq([r for r in valid_results if r.get("url") in failed_set])
            if retrying:
                logger.info(f"[STAGE2] {len(retrying)} failed URLs left pending for retry")

            if len(valid_results) > 0:
                # #586: dual-export; never raises, never silent on a sink failure.
                record_performance(
                    self.postgres,
                    stage="stage2",
                    urls_processed=len(valid_results),
                    processing_time_seconds=batch_time,
                    worker_count=self.max_concurrent,
                )

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

    async def _write_analysis(self, accepted: list[dict[str, Any]]) -> bool:
        """Upsert accepted analysis rows into stage2_page_analysis by url_hash (#311)."""
        rows = ensure_url_hash(accepted)
        update_columns = sorted({k for r in rows for k in r} - {"url_hash"})
        try:
            affected = await asyncio.to_thread(
                self.delta.merge_into, ANALYSIS_TABLE, rows, "url_hash", update_columns
            )
        except Exception as e:
            logger.error(f"[STAGE2] Analysis upsert raised: {e}")
            affected = -1
        if affected is None or affected < 0:
            if STAGE2_ANALYSIS_WRITE_FAILURES is not None:
                STAGE2_ANALYSIS_WRITE_FAILURES.inc()
            return False
        logger.info(f"[STAGE2] Upserted {len(rows)} analysis results")
        return True

    async def _update_queue_status(
        self, completed_urls: list[str], table_name: str = "stage2_queue", status: str = "completed"
    ) -> bool:
        """Set ``status`` (and ``completed_at`` as the finish time) for the given URLs.

        Uses a row-level Delta MERGE, retried with backoff because concurrent
        workers' commits conflict (#168). There is deliberately no full-table
        overwrite fallback: rewriting the whole queue from a stale read wiped
        other workers' updates. If every attempt fails, the rows simply stay
        ``pending`` (they are re-analysed next run) and
        ``stage2_queue_update_failures_total`` is incremented.
        """
        if not completed_urls:
            return True

        try:
            attempts = max(1, int(os.getenv("STAGE2_MERGE_RETRIES", DEFAULT_STAGE2_MERGE_RETRIES)))
        except ValueError:
            attempts = DEFAULT_STAGE2_MERGE_RETRIES
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                await asyncio.to_thread(self._merge_queue_status, completed_urls, table_name, status)
                logger.info(f"[STAGE2] Marked {len(completed_urls)} items as {status} in {table_name} via MERGE")
                return True
            except Exception as e:  # commit conflicts under concurrency are retryable
                last_error = e
                if attempt < attempts:
                    delay = 0.1 * (2 ** (attempt - 1))
                    logger.warning(
                        f"[STAGE2] Queue MERGE attempt {attempt}/{attempts} failed ({e}); retrying in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)

        if STAGE2_QUEUE_UPDATE_FAILURES is not None:
            STAGE2_QUEUE_UPDATE_FAILURES.labels(status=status).inc()
        logger.error(
            f"[STAGE2] Failed to mark {len(completed_urls)} items as {status} in {table_name} after "
            f"{attempts} MERGE attempts: {last_error}. Rows stay pending and will be retried next run."
        )
        return False

    def _merge_queue_status(self, urls: list[str], table_name: str, status: str) -> None:
        """One MERGE attempt against the current table version (re-read every call)."""
        updates_table = pa.Table.from_pydict(
            {
                "url": pa.array(urls, type=pa.string()),
                "status": pa.array([status] * len(urls), type=pa.string()),
                "completed_at": pa.array([datetime.now() for _ in urls], type=pa.timestamp("ms")),
            }
        )
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

    MAX_REDIRECTS = 10
    REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

    async def _get_guarded(self, session: Any, url: str) -> Any:
        """GET following redirects by hand so every hop passes the SSRF guard
        *before* a connection is made (#682). aiohttp's own
        ``allow_redirects=True`` would follow a public URL into 169.254.169.254
        or an in-cluster service unchecked."""
        current = url
        for _ in range(self.MAX_REDIRECTS + 1):
            reason = ssrf_block_reason(current)
            if reason is not None:
                raise SSRFBlocked(current, reason)
            response = await session.get(current, allow_redirects=False)
            location = response.headers.get("Location")
            if response.status in self.REDIRECT_STATUSES and location:
                response.release()
                current = urljoin(str(response.url), location)
                continue
            return response
        raise SSRFBlocked(current, "too_many_redirects")

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

        domain = domain_of(url)
        host_slot = self._host_slot(domain)
        if host_slot.locked() and STAGE2_HOST_THROTTLED is not None:
            STAGE2_HOST_THROTTLED.inc()
        # Per-host slot first, so URLs queued behind a busy host don't hold
        # global slots that other hosts could use (#195).
        async with host_slot, self.semaphore:
            backoff = self._backoff()
            if backoff.blocked(domain):
                count_deferred("stage2")
                return {"url": url, "url_hash": url_hash, "_deferred": True}
            breaker = self._breaker(domain)
            if not breaker.can_execute():
                # Host keeps failing: leave the row pending instead of burning attempts.
                count_deferred("stage2")
                self._count_fetch("circuit_open")
                return {"url": url, "url_hash": url_hash, "_deferred": True}
            try:
                session = self._session
                owns_session = session is None  # direct callers outside run()
                if session is None:
                    session = self._new_session()
                try:
                    return await self._fetch_with_retries(session, url, url_hash, is_heavy, domain, breaker)
                finally:
                    if owns_session:
                        await session.close()
            except aiohttp.ClientError as e:  # non-transient client errors fail fast
                error_type = f"ClientError: {type(e).__name__}"
                self._log_error_to_postgres(url, error_type, str(e))
                return self._error_record(url, url_hash, 0, error_type)
            except Exception as e:
                logger.error(f"Failed to analyze {url}: {e}")
                self._log_error_to_postgres(url, type(e).__name__, str(e))
                return self._error_record(url, url_hash, 0, f"error: {str(e)}")

    async def _fetch_with_retries(
        self,
        session: aiohttp.ClientSession,
        url: str,
        url_hash: str,
        is_heavy: bool,
        domain: str,
        breaker: CircuitBreaker,
    ) -> dict[str, Any]:
        """Fetch + analyse with bounded exponential backoff on transient failures (#158).

        Retried: timeouts, connection/payload errors and TRANSIENT_HTTP_STATUSES.
        Not retried: soft bans (quarantine + domain backoff), other 4xx, non-network errors.
        Exhausting every attempt counts one failure on the host's circuit breaker.
        """
        attempts = max(1, int(getattr(self, "http_attempts", DEFAULT_STAGE2_HTTP_ATTEMPTS)))
        code, message = 0, "timeout"
        for attempt in range(1, attempts + 1):
            retry_after: float | None = None
            try:
                result = await self._fetch_once(session, url, url_hash, is_heavy, domain)
            except SSRFBlocked as blocked:  # policy, not a transient failure: never retried
                count_blocked("stage2", blocked.reason)
                logger.warning(f"[STAGE2] SSRF guard blocked {blocked.url} ({blocked.reason})")
                return self._error_record(url, url_hash, 0, f"ssrf_blocked:{blocked.reason}")
            except TransientHTTPError as e:
                code, message, reason, retry_after = e.status, "http_error", f"http_{e.status}", e.retry_after
                exc_type, exc_text = "HTTPError", str(e)
            except TimeoutError as e:
                code, message, reason = 0, "timeout", "timeout"
                exc_type, exc_text = "TimeoutError", str(e)
            except (aiohttp.ClientConnectionError, aiohttp.ClientPayloadError) as e:
                code, message, reason = 0, f"ClientError: {type(e).__name__}", "connection"
                exc_type, exc_text = message, str(e)
            else:
                breaker.record_success()
                if attempt > 1:
                    logger.info(f"[STAGE2] {url[:80]} succeeded on attempt {attempt}/{attempts}")
                self._count_fetch("recovered" if attempt > 1 else "first_try")
                return result

            if attempt < attempts:
                delay = self._retry_delay(attempt, retry_after)
                logger.info(
                    f"[STAGE2] Transient {reason} for {url[:80]} (attempt {attempt}/{attempts}); "
                    f"retrying in {delay:.2f}s"
                )
                if STAGE2_HTTP_RETRIES is not None:
                    STAGE2_HTTP_RETRIES.labels(reason=reason).inc()
                await asyncio.sleep(delay)

        breaker.record_failure()
        self._count_fetch("exhausted")
        logger.warning(f"[STAGE2] {url[:80]} failed after {attempts} attempt(s): {message} (code={code})")
        self._log_error_to_postgres(url, exc_type, f"{exc_text} after {attempts} attempt(s)")
        return self._error_record(url, url_hash, code, message)

    async def _fetch_once(
        self, session: aiohttp.ClientSession, url: str, url_hash: str, is_heavy: bool, domain: str
    ) -> dict[str, Any]:
        """One GET. Raises TransientHTTPError / TimeoutError / ClientError for the retry loop."""
        response = await self._get_guarded(session, url)  # #682: every hop SSRF-checked
        async with response:
            if response.status >= 400:
                body = ""
                if response.status in (403, 503):
                    body = await self._read_error_body(response)
                sig = self._detector().detect(response.status, body, response.headers)
                if sig:  # soft bans are never retried in-request (#582)
                    return self._soft_ban_record(url, url_hash, response.status, sig, domain)
                if response.status in TRANSIENT_HTTP_STATUSES:
                    raise TransientHTTPError(response.status, _parse_retry_after(response.headers.get("Retry-After")))
                return self._error_record(url, url_hash, response.status, "http_error")

            content_type = response.headers.get("Content-Type", "").lower()

            if "text/html" in content_type:
                html = await response.text()
                sig = self._detector().detect(response.status, html, response.headers)
                if sig:  # challenge page served as 200: never analysed as content
                    return self._soft_ban_record(url, url_hash, response.status, sig, domain)
                return await self._analyze_html(url, url_hash, html, is_heavy)
            elif "application/pdf" in content_type:
                return self._route_pdf_to_stage4(url, url_hash)
            else:
                return self._minimal_record(url, url_hash, content_type)

    def _retry_delay(self, attempt: int, retry_after: float | None = None) -> float:
        """Exponential backoff with jitter, capped; a numeric Retry-After raises it (also capped)."""
        cap = float(getattr(self, "http_backoff_max", DEFAULT_STAGE2_HTTP_BACKOFF_MAX))
        base = min(cap, float(getattr(self, "http_backoff_base", DEFAULT_STAGE2_HTTP_BACKOFF_BASE)) * (2 ** (attempt - 1)))
        delay: float = base * (0.5 + random.random() / 2)
        if retry_after is not None:
            delay = max(delay, min(float(retry_after), cap))
        return delay

    def _breaker(self, domain: str) -> CircuitBreaker:
        breakers: dict[str, CircuitBreaker] | None = getattr(self, "_host_breakers", None)
        if breakers is None:
            breakers = self._host_breakers = {}
        breaker: CircuitBreaker | None = breakers.get(domain)
        if breaker is None:
            breaker = breakers[domain] = CircuitBreaker(
                failure_threshold=int(getattr(self, "breaker_failures", DEFAULT_STAGE2_BREAKER_FAILURES)),
                recovery_timeout=int(getattr(self, "breaker_recovery", DEFAULT_STAGE2_BREAKER_RECOVERY)),
                name=f"stage2:{domain}",
            )
        return breaker

    @staticmethod
    def _count_fetch(outcome: str) -> None:
        if STAGE2_HTTP_FETCHES is not None:
            STAGE2_HTTP_FETCHES.labels(outcome=outcome).inc()

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

    # -------------------------------------------------------- soft ban (#582)
    def _detector(self) -> SoftBanDetector:
        if getattr(self, "soft_ban", None) is None:
            self.soft_ban = SoftBanDetector()
        return self.soft_ban

    def _backoff(self) -> DomainBackoff:
        if getattr(self, "domain_backoff", None) is None:
            self.domain_backoff = DomainBackoff(stage="stage2")
        return self.domain_backoff

    @staticmethod
    async def _read_error_body(response: Any) -> str:
        try:
            raw = await response.content.read(65536)
            return str(raw.decode("utf-8", errors="replace"))
        except Exception:
            try:
                return str(await response.text())[:65536]
            except Exception:
                return ""

    def _soft_ban_record(self, url: str, url_hash: str, status: int, signature: str, domain: str) -> dict[str, Any]:
        """Quarantine a soft-ban response (stage2_errors) and feed the domain backoff."""
        count_soft_ban("stage2", signature)
        self._backoff().record(domain)
        logger.warning(f"[STAGE2] Soft ban ({signature}, HTTP {status}) for {url[:80]}; quarantined")
        return self._error_record(url, url_hash, status, f"{SOFT_BAN_PREFIX}{signature}")

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
        """Export a per-URL error: Prometheus always, Postgres best effort (#586)."""
        record_error(
            self.postgres,
            stage="stage2",
            url=url,
            error_type=error_type,
            error_message=error_message,
            http_status_code=http_status,
        )

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
