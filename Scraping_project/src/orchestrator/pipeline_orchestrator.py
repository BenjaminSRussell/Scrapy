import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from src.core.constants import LEGACY_TABLE_STAGE3_SUMMARIES, TABLE_STAGE3_SUMMARIES
from src.utils.delta import get_delta
from src.stage2.stage2_worker import Stage2Worker
from src.stage3.stage3_worker import Stage3Worker
from src.stage4.stage4_worker import Stage4Worker

logger = logging.getLogger(__name__)

RunStatus = Literal["pending", "running", "complete", "partial_failed", "failed"]

try:  # metric/alert hook for partial or failed runs (#521)
    from prometheus_client import Counter

    PIPELINE_RUNS = Counter(
        "pipeline_runs_total",
        "Full pipeline runs by final status (complete/partial_failed/failed).",
        ["status"],
    )
except Exception:  # prometheus_client missing or metric already registered
    PIPELINE_RUNS = None


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
    stage2_pages_analyzed: int = 0
    stage2_quality_docs: int = 0
    stage2_massive_docs: int = 0
    stage3_summaries_created: int = 0
    stage4_large_summaries: int = 0
    start_time: datetime | None = None
    end_time: datetime | None = None
    status: RunStatus = "pending"
    stage_errors: dict[str, str] = field(default_factory=dict)

    @property
    def total_duration_seconds(self) -> float:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0

class PipelineOrchestrator:

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.delta = get_delta()
        self.stats = PipelineStats()

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

        settings.set('EXTENSIONS', {})

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
                summary_count += len(self.delta.read(table) or [])
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
            large_summaries = self.delta.read("stage4_large_doc_summaries")
            large_count = len(large_summaries)

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

        Returns:
            The run's PipelineStats, including ``status`` and ``stage_errors``.
        """
        self.stats.start_time = datetime.now()
        self.stats.status = "running"
        self.stats.stage_errors = {}

        logger.info(" " * 40)
        logger.info("STARTING FULL PIPELINE EXECUTION")
        logger.info(" " * 40)

        try:
            try:
                self.run_stage1(url_limit=stage1_url_limit)
            except Exception as e:
                self.stats.stage_errors["stage1"] = repr(e)
                raise
            try:
                await self.run_stage2(max_concurrent=stage2_concurrent)
            except Exception as e:
                self.stats.stage_errors["stage2"] = repr(e)
                raise
        except Exception:
            self._finish("failed")
            raise PipelineRunError("failed", self.stats.stage_errors) from None

        results = await asyncio.gather(
            self.run_stage3(max_concurrent=stage3_concurrent),
            self.run_stage4(),
            return_exceptions=True,
        )
        for name, result in zip(("stage3", "stage4"), results):
            if isinstance(result, BaseException):
                self.stats.stage_errors[name] = repr(result)
                logger.error(f"Pipeline {name} failed: {result!r}")

        failed = [n for n in ("stage3", "stage4") if n in self.stats.stage_errors]
        status: RunStatus = (
            "complete" if not failed else "failed" if len(failed) == 2 else "partial_failed"
        )
        self._finish(status)
        self._print_final_stats()

        if status == "complete" or (status == "partial_failed" and allow_partial):
            return self.stats
        raise PipelineRunError(status, self.stats.stage_errors)

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
