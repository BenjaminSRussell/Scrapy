import asyncio
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from src.core.constants import LEGACY_TABLE_STAGE3_SUMMARIES, TABLE_STAGE3_SUMMARIES
from src.utils.delta import get_delta
from src.orchestrator.hop_reconciliation import BARRIER_MODES, alert_kind, count_hops, reconcile
from src.stage1.js_queue import count_pending, js_spider_enabled
from src.stage2.stage2_worker import Stage2Worker
from src.stage3.stage3_worker import Stage3Worker
from src.stage4.stage4_worker import Stage4Worker

logger = logging.getLogger(__name__)

RunStatus = Literal["pending", "running", "complete", "partial_failed", "failed", "aborted"]

# Interrupts that end a run without a stage "failing": Ctrl-C, sys.exit / SIGTERM
# handlers, and task cancellation. They are recorded as status "aborted" (#684).
_ABORTS = (KeyboardInterrupt, SystemExit, asyncio.CancelledError)

try:  # metric/alert hook for partial or failed runs (#521)
    from prometheus_client import Counter

    PIPELINE_RUNS = Counter(
        "pipeline_runs_total",
        "Full pipeline runs by final status (complete/partial_failed/failed/aborted).",
        ["status"],
    )
except Exception:  # prometheus_client missing or metric already registered
    PIPELINE_RUNS = None

try:  # stage2_queue hop reconciliation alerts (#646)
    from prometheus_client import Counter as _Counter

    PIPELINE_HOP_ALERTS = _Counter(
        "pipeline_hop_alerts_total",
        "Hop reconciliation alerts by kind (hop_lost/stage2_pending/late_append).",
        ["alert"],
    )
except Exception:
    PIPELINE_HOP_ALERTS = None


class PipelineRunError(RuntimeError):
    """Raised when a full pipeline run does not complete (#521)."""

    def __init__(self, status: str, stage_errors: dict[str, str]):
        self.status = status
        self.stage_errors = dict(stage_errors)
        detail = ", ".join(f"{k}: {v}" for k, v in stage_errors.items()) or "unknown"
        super().__init__(f"Pipeline run {status}: {detail}")

def _is_count(value: object) -> bool:
    """True for a real int count (not bool, not a test-double return value)."""
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass
class PipelineStats:
    stage1_urls_discovered: int = 0
    stage1_urls_queued: int = 0
    stage1_js_pending: int = 0
    stage1_js_drain_error: str | None = None
    stage2_watermark: str | None = None
    hop_funnel: dict | None = None
    stage2_pages_analyzed: int = 0
    stage2_quality_docs: int = 0
    stage2_massive_docs: int = 0
    stage3_summaries_created: int = 0
    stage4_large_summaries: int = 0
    start_time: datetime | None = None
    end_time: datetime | None = None
    status: RunStatus = "pending"
    stage_errors: dict[str, str] = field(default_factory=dict)
    # Stage that was running when the run was aborted (#684); None otherwise.
    aborted_stage: str | None = None

    @property
    def total_duration_seconds(self) -> float:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0

# Scrapy project root (holds scrapy.cfg); the JS drain runs ``scrapy crawl`` there.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_JS_DRAIN_TIMEOUT_S = 3600


def _set_js_queue_pending_metric(count: int) -> None:
    """Best-effort update of the ``pipeline_js_queue_pending`` gauge (#645)."""
    try:
        from src.scrapy_prometheus import set_pipeline_js_queue_pending

        set_pipeline_js_queue_pending(count)
    except Exception as e:  # metrics are optional
        logger.debug("Could not update pipeline_js_queue_pending: %s", e)


class PipelineOrchestrator:

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.delta = get_delta()
        self.stats = PipelineStats()
        self._current_stage: str | None = None

    def count_js_queue_pending(self) -> int:
        """Pending rows in Delta ``js_spider_queue`` (missing status = pending)."""
        try:
            return count_pending(self.delta.read("js_spider_queue"))
        except Exception as e:
            logger.warning("Could not read js_spider_queue: %s", e)
            return 0

    def _js_drain_timeout(self) -> float:
        raw = os.environ.get("JS_DRAIN_TIMEOUT_SECONDS") or self.config.get("js_drain_timeout_seconds")
        try:
            return float(raw) if raw else float(_JS_DRAIN_TIMEOUT_S)
        except (TypeError, ValueError):
            return float(_JS_DRAIN_TIMEOUT_S)

    def run_js_queue(self, enabled: bool | None = None) -> int:
        """Stage 1b (#645): drain ``js_spider_queue`` with the ``javascript`` spider.

        The spider runs in a child ``scrapy crawl`` process: Stage 1 has already
        started (and stopped) this process's Twisted reactor, which cannot be
        restarted. Returns the pending count left afterwards (0 when the JS
        path is off or there was nothing to drain).
        """
        logger.info("=" * 80)
        logger.info("STAGE 1b: JS SPIDER QUEUE DRAIN")
        logger.info("=" * 80)

        if enabled is None:
            enabled = self.config.get("enable_js_spider")
        pending = self.count_js_queue_pending()
        self.stats.stage1_js_pending = pending
        _set_js_queue_pending_metric(pending)

        if not js_spider_enabled(enabled=enabled):
            logger.info("JS spider path disabled (stage1.enable_js_spider / ENABLE_JS_SPIDER); skipping drain")
            if pending:
                logger.warning(
                    "js_spider_queue has %s pending item(s) but the JS path is off; they will not be drained",
                    pending,
                )
            return 0
        if not pending:
            logger.info("js_spider_queue has no pending items; nothing to drain")
            return 0

        logger.info("Draining js_spider_queue: %s pending item(s)", pending)
        cmd = [sys.executable, "-m", "scrapy", "crawl", "javascript"]
        try:
            proc = subprocess.run(cmd, cwd=str(_PROJECT_ROOT), timeout=self._js_drain_timeout(), check=False)
            if proc.returncode != 0:
                self.stats.stage1_js_drain_error = f"javascript spider exited {proc.returncode}"
                logger.warning("JS queue drain: %s", self.stats.stage1_js_drain_error)
        except subprocess.TimeoutExpired:
            self.stats.stage1_js_drain_error = "javascript spider timed out"
            logger.warning("JS queue drain timed out after %.0fs", self._js_drain_timeout())

        remaining = self.count_js_queue_pending()
        self.stats.stage1_js_pending = remaining
        _set_js_queue_pending_metric(remaining)
        logger.info(" JS queue drain complete: %s pending remaining (was %s)", remaining, pending)
        return remaining

    def run_stage1(
        self,
        spider_name: str = "scout",
        url_limit: int | None = None,
    ) -> int:
        """Run Stage 1 (URL Discovery) with Scout spider.

        Args:
            spider_name: Spider to run (scout, deep_dive, or javascript)
            url_limit: Max items to scrape (None = unlimited)

        Returns:
            Number of URLs queued for Stage 2
        """
        logger.info("=" * 80)
        logger.info("STAGE 1: URL DISCOVERY")
        logger.info("=" * 80)

        settings = get_project_settings()

        # Only the memory soft-stop (#539): drain before the OOM killer even here.
        settings.set('EXTENSIONS', {"src.memory_soft_stop.MemorySoftStop": 530})

        if url_limit:
            settings.set('CLOSESPIDER_ITEMCOUNT', url_limit)

        settings.set('TWISTED_REACTOR', 'twisted.internet.selectreactor.SelectReactor')

        process = CrawlerProcess(settings)
        process.crawl(spider_name)
        process.start()

        try:
            queue = self.delta.read("stage2_queue")
            queued_count = len([item for item in queue if item.get('status') == 'pending'])
            logger.info(f" Stage 1 complete: {queued_count} URLs queued for Stage 2")
            self.stats.stage1_urls_queued = queued_count
            return queued_count
        except Exception as e:
            logger.warning(f"Could not read stage2_queue: {e}")
            return 0

    async def run_stage2(
        self,
        max_concurrent: int = 50,
        batch_size: int = 100,
    ) -> int:
        """Run Stage 2 (Page Analysis).

        Args:
            max_concurrent: Max concurrent HTTP requests
            batch_size: Batch size for processing

        Returns:
            Number of pages analyzed
        """
        logger.info("=" * 80)
        logger.info("STAGE 2: PAGE ANALYSIS")
        logger.info("=" * 80)

        worker = Stage2Worker(max_concurrent=max_concurrent, batch_size=batch_size)
        run_counts = await worker.run()

        if isinstance(run_counts, dict) and isinstance(run_counts.get("analyzed"), int):
            # Per-run counts from the worker (#327): not cumulative table totals.
            analyzed_count = run_counts["analyzed"]
            self.stats.stage2_pages_analyzed = analyzed_count
            self.stats.stage2_quality_docs = int(run_counts.get("quality_docs", 0))
            self.stats.stage2_massive_docs = int(run_counts.get("massive_docs", 0))
            logger.info(f" Stage 2 complete: {analyzed_count} pages analyzed this run")
            logger.info(f"   - Quality docs: {self.stats.stage2_quality_docs}")
            logger.info(f"   - Massive docs: {self.stats.stage2_massive_docs}")
            return analyzed_count

        try:
            analysis = self.delta.read("stage2_page_analysis")
            analyzed_count = len(analysis)

            quality_docs = len([d for d in analysis if not d.get('is_massive_doc', False) and not d.get('is_low_quality', True)])
            massive_docs = len([d for d in analysis if d.get('is_massive_doc', False)])

            logger.info(f" Stage 2 complete: {analyzed_count} pages analyzed")
            logger.info(f"   - Quality docs: {quality_docs}")
            logger.info(f"   - Massive docs: {massive_docs}")

            self.stats.stage2_pages_analyzed = analyzed_count
            self.stats.stage2_quality_docs = quality_docs
            self.stats.stage2_massive_docs = massive_docs

            return analyzed_count
        except Exception as e:
            logger.warning(f"Could not read stage2_page_analysis: {e}")
            return 0

    async def run_stage3(
        self,
        max_concurrent: int = 20,
        batch_size: int = 50,
    ) -> int:
        """Run Stage 3 (Summarization for quality docs).

        Args:
            max_concurrent: Max concurrent summarization tasks
            batch_size: Batch size for processing

        Returns:
            Number of summaries created
        """
        logger.info("=" * 80)
        logger.info("STAGE 3: SUMMARIZATION")
        logger.info("=" * 80)

        worker = Stage3Worker(max_concurrent=max_concurrent, batch_size=batch_size)
        written = await worker.run()

        if _is_count(written):
            logger.info(f" Stage 3 complete: {written} summaries created this run")
            self.stats.stage3_summaries_created = written
            return written

        # Canonical Stage 3 table plus the pre-#612 legacy name, so a lake
        # written under the old name still reports its summaries.
        summary_count = 0
        for table in (TABLE_STAGE3_SUMMARIES, LEGACY_TABLE_STAGE3_SUMMARIES):
            try:
                summary_count += int(self.delta.count(table) or 0)  # metadata count, no full read (#372)
            except Exception as e:
                logger.warning(f"Could not read {table}: {e}")

        logger.info(f" Stage 3 complete: {summary_count} summaries created")
        self.stats.stage3_summaries_created = summary_count
        return summary_count

    async def run_stage4(self) -> int:
        logger.info("=" * 80)
        logger.info("STAGE 4: LARGE DOCUMENT PROCESSING")
        logger.info("=" * 80)

        worker = Stage4Worker()
        written = await worker.run()

        if _is_count(written):
            logger.info(f" Stage 4 complete: {written} large doc summaries created this run")
            self.stats.stage4_large_summaries = written
            return written

        try:
            # metadata count, no full read (#372)
            large_count = int(self.delta.count("stage4_large_doc_summaries") or 0)

            logger.info(f" Stage 4 complete: {large_count} large doc summaries created")
            self.stats.stage4_large_summaries = large_count

            return large_count
        except Exception as e:
            logger.warning(f"Could not read stage4_large_doc_summaries: {e}")
            return 0

    async def run_full_pipeline(
        self,
        stage1_url_limit: int | None = 100,
        stage2_concurrent: int = 50,
        stage3_concurrent: int = 20,
        allow_partial: bool = False,
    ) -> PipelineStats:
        """Run the complete 4-stage pipeline.

        Stage 1 and Stage 2 are required: a failure stops the run with status
        ``failed``. Stage 3 and Stage 4 run concurrently and both are awaited
        even if one fails. One failure gives ``partial_failed``; both give
        ``failed``. Unless ``allow_partial`` is set, any status other than
        ``complete`` raises :class:`PipelineRunError`, so the process exits
        non-zero instead of reporting an incomplete lake as finished (#521).

        Args:
            stage1_url_limit: Max URLs to discover in Stage 1
            stage2_concurrent: Concurrency for Stage 2
            stage3_concurrent: Concurrency for Stage 3
            allow_partial: Return (not raise) on a ``partial_failed`` run

        Abort policy (#684): ``KeyboardInterrupt``, ``SystemExit`` and task
        cancellation end the run with status ``aborted`` (``aborted_stage`` names
        the stage, ``end_time`` is set, ``pipeline_runs_total{status="aborted"}``
        is incremented) and are re-raised. Concurrent Stage 3/4 work is cancelled
        with the run. Work in flight is not marked done: Stage 2 only acks queue
        rows after a durable upsert, so they stay ``pending`` and the next run
        picks them up. An interrupt after the outcome is final (all stages done)
        keeps that outcome. Every run starts from fresh ``PipelineStats``, so
        nothing from an aborted run leaks into the next one.

        Returns:
            The run's PipelineStats, including ``status`` and ``stage_errors``.
        """
        # Fresh stats per run: counts from an earlier (possibly aborted) run must
        # not be reported as this run's (#684).
        self.stats = PipelineStats(start_time=datetime.now(), status="running")
        self._current_stage = None
        try:
            return await self._run_full_pipeline(
                stage1_url_limit, stage2_concurrent, stage3_concurrent, allow_partial
            )
        except _ABORTS as e:
            if self.stats.status != "running":
                raise  # outcome already final (e.g. interrupted while printing stats): keep it
            stage = self._current_stage or "startup"
            self.stats.aborted_stage = stage
            self.stats.stage_errors.setdefault(stage, f"aborted: {type(e).__name__}")
            self._finish("aborted")
            raise
        finally:
            self._current_stage = None

    async def _run_full_pipeline(
        self,
        stage1_url_limit: int | None,
        stage2_concurrent: int,
        stage3_concurrent: int,
        allow_partial: bool,
    ) -> PipelineStats:
        logger.info(" " * 40)
        logger.info("STARTING FULL PIPELINE EXECUTION")
        logger.info(" " * 40)

        try:
            self._current_stage = "stage1"
            try:
                self.run_stage1(url_limit=stage1_url_limit)
            except Exception as e:
                self.stats.stage_errors["stage1"] = repr(e)
                raise
            # Stage 1b (#645): render what Scout queued for the JS spider. A drain
            # problem is logged and recorded, not fatal: Stage 2 still has its queue.
            self._current_stage = "stage1b"
            try:
                self.run_js_queue()
            except Exception as e:
                self.stats.stage1_js_drain_error = repr(e)
                logger.error(f"JS queue drain failed (continuing with Stage 2): {e!r}")
            self._current_stage = "stage2"
            queue_before = self._stage2_queue_rows()
            try:
                await self.run_stage2(max_concurrent=stage2_concurrent)
            except Exception as e:
                self.stats.stage_errors["stage2"] = repr(e)
                raise
        except Exception:
            self._finish("failed")
            raise PipelineRunError("failed", self.stats.stage_errors) from None

        # Stage 2 -> 3/4 barrier + hop reconciliation (#646).
        recon = self._stage2_barrier(queue_before)
        if recon is not None and recon.stage2_watermark is None:
            pending = recon.hops.still_pending + recon.hops.late_appends
            self.stats.stage_errors["stage2_barrier"] = (
                f"strict barrier: {pending} stage2_queue row(s) still pending; Stage 3/4 not started"
            )
            self._current_stage = None
            self._finish("failed")
            self._print_final_stats()
            raise PipelineRunError("failed", self.stats.stage_errors)

        results: list[object]
        if self._setting("stage3_4_parallel", "STAGE3_4_PARALLEL", "true").lower() in ("1", "true", "yes", "on"):
            self._current_stage = "stage3+stage4"
            results = list(await asyncio.gather(
                self.run_stage3(max_concurrent=stage3_concurrent),
                self.run_stage4(),
                return_exceptions=True,
            ))
        else:  # sequential: Stage 4 starts after Stage 3, whatever Stage 3's outcome
            results = []
            for name, start in (("stage3", lambda: self.run_stage3(max_concurrent=stage3_concurrent)), ("stage4", self.run_stage4)):
                self._current_stage = name
                try:
                    results.append(await start())
                except Exception as e:
                    results.append(e)
        for name, result in zip(("stage3", "stage4"), results):
            if isinstance(result, BaseException):
                self.stats.stage_errors[name] = repr(result)
                logger.error(f"Pipeline {name} failed: {result!r}")

        self._current_stage = None
        failed = [n for n in ("stage3", "stage4") if n in self.stats.stage_errors]
        status: RunStatus = (
            "complete" if not failed else "failed" if len(failed) == 2 else "partial_failed"
        )
        if recon is not None and not recon.within_tolerance:
            # Silent loss is not success (#646): at best partial, never complete.
            self.stats.stage_errors["reconciliation"] = "; ".join(
                a for a in recon.alerts if alert_kind(a) == "hop_lost"
            )
            if status == "complete":
                status = "partial_failed"
        self._finish(status)
        self._print_final_stats()

        if status == "complete" or (status == "partial_failed" and allow_partial):
            return self.stats
        raise PipelineRunError(status, self.stats.stage_errors)

    def _setting(self, key: str, env: str, default: str) -> str:
        value = os.environ.get(env)
        if value is None or not value.strip():
            value = self.config.get(key, default)
        return str(value).strip()

    def _stage2_queue_rows(self) -> list[dict] | None:
        """Snapshot of ``stage2_queue`` for hop accounting; None when unreadable."""
        if self._setting("stage2_barrier", "STAGE2_BARRIER", "flag").lower() == "off":
            return None
        try:
            rows = self.delta.read("stage2_queue")
        except Exception as e:
            logger.warning(f"[HOPS] Could not read stage2_queue: {e}")
            return None
        return rows if isinstance(rows, list) else None

    def _stage2_barrier(self, before: list[dict] | None):
        """Count the Stage 2 funnel and apply the barrier (#646).

        Modes (``stage2_barrier`` / ``$STAGE2_BARRIER``): ``flag`` (default) alerts
        on rows still pending and starts Stage 3/4; ``strict`` refuses to start
        them while any ``stage2_queue`` row is pending; ``off`` skips accounting.
        Returns None when there is nothing to reconcile.
        """
        mode = self._setting("stage2_barrier", "STAGE2_BARRIER", "flag").lower()
        if mode not in BARRIER_MODES:
            logger.warning(f"[HOPS] Unknown stage2_barrier {mode!r}; using 'flag'")
            mode = "flag"
        if mode == "off" or before is None:
            return None
        after = self._stage2_queue_rows()
        if after is None:
            return None
        try:
            tolerance = max(0, int(self._setting("hop_tolerance", "HOP_TOLERANCE", "0")))
        except ValueError:
            tolerance = 0
        hops = count_hops(before, after, discovered=self.stats.stage1_urls_queued)
        recon = reconcile(hops, tolerance=tolerance, barrier=mode)
        for alert in recon.alerts:
            logger.warning(f"[HOPS] {alert}")
            if PIPELINE_HOP_ALERTS is not None:
                PIPELINE_HOP_ALERTS.labels(alert=alert_kind(alert)).inc()
        if not (mode == "strict" and (hops.still_pending or hops.late_appends)):
            recon.stage2_watermark = datetime.now().isoformat()
        logger.info(f"[HOPS] Stage 2 funnel: {hops.to_dict()} watermark={recon.stage2_watermark}")
        self.stats.stage2_watermark = recon.stage2_watermark
        self.stats.hop_funnel = recon.panel()
        return recon

    def _finish(self, status: RunStatus) -> None:
        self.stats.end_time = datetime.now()
        self.stats.status = status
        if PIPELINE_RUNS is not None:
            PIPELINE_RUNS.labels(status=status).inc()
        if status != "complete":
            logger.error(f"Pipeline run {status}: {self.stats.stage_errors}")

    def run_stage_by_name(
        self,
        stage: Literal["stage1", "stage2", "stage3", "stage4"],
        **kwargs
    ):
        """Run a specific stage by name.

        Args:
            stage: Stage name (stage1, stage2, stage3, or stage4)
            **kwargs: Stage-specific arguments
        """
        if stage == "stage1":
            return self.run_stage1(**kwargs)
        elif stage == "stage2":
            return asyncio.run(self.run_stage2(**kwargs))
        elif stage == "stage3":
            return asyncio.run(self.run_stage3(**kwargs))
        elif stage == "stage4":
            return asyncio.run(self.run_stage4(**kwargs))
        else:
            raise ValueError(f"Unknown stage: {stage}")

    def _print_final_stats(self):
        logger.info("\n" + "=" * 80)
        logger.info(f"PIPELINE EXECUTION FINISHED: {self.stats.status.upper()}")
        logger.info("=" * 80)
        logger.info(f"Duration: {self.stats.total_duration_seconds:.2f} seconds")
        logger.info("")
        logger.info(" FINAL STATISTICS:")
        logger.info("-" * 80)
        logger.info("  Stage 1 (URL Discovery):")
        logger.info(f"    - URLs queued for Stage 2: {self.stats.stage1_urls_queued}")
        logger.info("")
        logger.info("  Stage 2 (Page Analysis):")
        logger.info(f"    - Pages analyzed: {self.stats.stage2_pages_analyzed}")
        logger.info(f"    - Quality docs → Stage 3: {self.stats.stage2_quality_docs}")
        logger.info(f"    - Massive docs → Stage 4: {self.stats.stage2_massive_docs}")
        logger.info("")
        logger.info("  Stage 3 (Summarization):")
        logger.info(f"    - Summaries created: {self.stats.stage3_summaries_created}")
        logger.info("")
        logger.info("  Stage 4 (Large Docs):")
        logger.info(f"    - Large doc summaries: {self.stats.stage4_large_summaries}")
        logger.info("=" * 80)
        logger.info(f" Total summaries created: {self.stats.stage3_summaries_created + self.stats.stage4_large_summaries}")
        logger.info("=" * 80 + "\n")

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    orchestrator = PipelineOrchestrator()

    await orchestrator.run_full_pipeline(
        stage1_url_limit=50,
        stage2_concurrent=10,
        stage3_concurrent=5,
    )

if __name__ == "__main__":
    asyncio.run(main())
