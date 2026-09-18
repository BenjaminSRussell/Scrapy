"""Pipeline orchestrator: hop reconciliation + Stage2→3/4 barrier (#646)."""
from __future__ import annotations
import asyncio, logging, uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings
from src.core.exceptions import ErrorCategory, ErrorSeverity, PipelineException
from src.orchestrator.hop_reconciliation import (
    HopCounters, ReconciliationResult, hop_funnel_panel, reconcile_hops,
)
from src.stage2.stage2_worker import Stage2Worker
from src.stage3.stage3_worker import Stage3Worker
from src.stage4.stage4_worker import Stage4Worker
from src.utils.delta import get_delta

logger = logging.getLogger(__name__)
STAGE2_QUEUE_TABLE = "stage2_queue"
STAGE2_ANALYSIS_TABLE = "stage2_page_analysis"
STAGE3_SUMMARIES_TABLE = "stage4_summaries"
STAGE4_SUMMARIES_TABLE = "stage4_large_doc_summaries"
STAGE2_DLQ_TABLE = "stage2_dlq"

class Stage2BarrierError(PipelineException):
    def __init__(self, message: str, **kwargs: Any):
        super().__init__(message, category=ErrorCategory.DATA_CORRUPTION, severity=ErrorSeverity.HIGH, retryable=False, **kwargs)

class ReconciliationError(PipelineException):
    def __init__(self, message: str, **kwargs: Any):
        super().__init__(message, category=ErrorCategory.DATA_CORRUPTION, severity=ErrorSeverity.HIGH, retryable=False, **kwargs)

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
    crawl_job_id: str | None = None
    hops: HopCounters = field(default_factory=HopCounters)
    reconciliation: ReconciliationResult | None = None
    success: bool = False
    job_status: str = "pending"
    allow_partial: bool = False
    alerts: list[str] = field(default_factory=list)
    stage2_watermark: str | None = None
    stage2_pending_at_barrier: int = 0
    late_appends_flagged: int = 0
    stage3_4_parallel: bool = False

    @property
    def total_duration_seconds(self) -> float:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0

    def hop_funnel(self) -> dict[str, Any]:
        if self.reconciliation is not None:
            panel = hop_funnel_panel(self.reconciliation)
            panel["success"] = self.success
            panel["job_status"] = self.job_status
            return panel
        return {
            "panel": "hop_funnel", "crawl_job_id": self.crawl_job_id,
            "funnel": self.hops.to_dict(), "success": self.success,
            "job_status": self.job_status, "alerts": list(self.alerts),
            "stage2_watermark": self.stage2_watermark,
            "late_appends_flagged": self.late_appends_flagged,
        }

class PipelineOrchestrator:
    """Stage1→4 with Stage2 barrier and hop reconciliation (#646)."""

    def __init__(self, config: dict | None = None):
        self.config = dict(config or {})
        self.delta = get_delta()
        self.stats = PipelineStats()
        self._apply_orchestrator_defaults()

    def _apply_orchestrator_defaults(self) -> None:
        orch = self.config.get("orchestrator")
        src = {**orch, **{k: v for k, v in self.config.items() if k != "orchestrator"}} if isinstance(orch, dict) else self.config
        self.allow_partial = bool(src.get("allow_partial", False))
        self.stage3_4_parallel = bool(src.get("stage3_4_parallel", False))  # default false until barrier proven
        self.stage2_barrier_enabled = bool(src.get("stage2_barrier_enabled", True))
        self.batch_mode = bool(src.get("batch_mode", True))
        self.hop_reconciliation_tolerance = int(src.get("hop_reconciliation_tolerance", 0))
        self.late_data_policy = str(src.get("late_data_policy", "flag"))  # #310 hook
        self.stats.allow_partial = self.allow_partial
        self.stats.stage3_4_parallel = self.stage3_4_parallel

    def run_stage1(self, spider_name: str = "scout", url_limit: int | None = None) -> int:
        logger.info("STAGE 1: URL DISCOVERY")
        settings = get_project_settings()
        settings.set("EXTENSIONS", {})
        if url_limit:
            settings.set("CLOSESPIDER_ITEMCOUNT", url_limit)
        settings.set("TWISTED_REACTOR", "twisted.internet.selectreactor.SelectReactor")
        process = CrawlerProcess(settings)
        process.crawl(spider_name)
        process.start()
        try:
            queue = self.delta.read(STAGE2_QUEUE_TABLE)
            queued = len([i for i in queue if i.get("status") == "pending"])
            discovered = len(queue)
            self.stats.stage1_urls_queued = queued
            self.stats.stage1_urls_discovered = discovered
            self.stats.hops.discovered = discovered
            self.stats.hops.enqueued = discovered
            return queued
        except Exception as e:
            logger.warning("Could not read %s: %s", STAGE2_QUEUE_TABLE, e)
            return 0

    async def run_stage2(self, max_concurrent: int = 50, batch_size: int = 100) -> int:
        logger.info("STAGE 2: PAGE ANALYSIS")
        pending_before = self._count_stage2_pending()
        await Stage2Worker(max_concurrent=max_concurrent, batch_size=batch_size).run()
        try:
            analysis = self.delta.read(STAGE2_ANALYSIS_TABLE)
            n = len(analysis)
            failed = len([d for d in analysis if d.get("has_error")])
            self.stats.stage2_pages_analyzed = n
            self.stats.stage2_quality_docs = len([d for d in analysis if not d.get("is_massive_doc") and not d.get("is_low_quality", True)])
            self.stats.stage2_massive_docs = len([d for d in analysis if d.get("is_massive_doc")])
            claimed = max(pending_before - self._count_stage2_pending(), n)
            self.stats.hops.claimed = max(self.stats.hops.claimed, claimed)
            self.stats.hops.ok = n - failed
            self.stats.hops.failed = failed
            self.stats.hops.dlq = self._count_dlq()
            return n
        except Exception as e:
            logger.warning("Could not read %s: %s", STAGE2_ANALYSIS_TABLE, e)
            if pending_before > 0 and self._count_stage2_pending() < pending_before:
                self.stats.alerts.append("hop_alert: Stage2 write failure (analysis unreadable after claim)")
            return 0

    async def run_stage3(self, max_concurrent: int = 20, batch_size: int = 50) -> int:
        self._refuse_if_stage2_pending("stage3")
        logger.info("STAGE 3: SUMMARIZATION")
        await Stage3Worker(max_concurrent=max_concurrent, batch_size=batch_size).run()
        try:
            summaries = self.delta.read(STAGE3_SUMMARIES_TABLE)
            self.stats.stage3_summaries_created = len(summaries)
            return len(summaries)
        except Exception as e:
            logger.warning("Could not read %s: %s", STAGE3_SUMMARIES_TABLE, e)
            return 0

    async def run_stage4(self) -> int:
        self._refuse_if_stage2_pending("stage4")
        logger.info("STAGE 4: LARGE DOCUMENT PROCESSING")
        await Stage4Worker().run()
        try:
            rows = self.delta.read(STAGE4_SUMMARIES_TABLE)
            self.stats.stage4_large_summaries = len(rows)
            return len(rows)
        except Exception as e:
            logger.warning("Could not read %s: %s", STAGE4_SUMMARIES_TABLE, e)
            return 0

    async def run_full_pipeline(self, stage1_url_limit: int | None = 100, stage2_concurrent: int = 50, stage3_concurrent: int = 20) -> PipelineStats:
        """Success requires hop reconciliation unless allow_partial; Stage3∥4 opt-in."""
        self.stats = PipelineStats()
        self._apply_orchestrator_defaults()
        self.stats.start_time = datetime.now(timezone.utc)
        self.stats.crawl_job_id = self._ensure_crawl_job_id()
        self.stats.job_status = "running"
        self.stats.allow_partial = self.allow_partial
        self.stats.stage3_4_parallel = self.stage3_4_parallel
        try:
            self.run_stage1(url_limit=stage1_url_limit)
            await self.run_stage2(max_concurrent=stage2_concurrent)
            if self.stage2_barrier_enabled:
                self.enforce_stage2_barrier()
            if self.stage3_4_parallel:
                await asyncio.gather(self.run_stage3(max_concurrent=stage3_concurrent), self.run_stage4())
            else:
                await self.run_stage3(max_concurrent=stage3_concurrent)
                await self.run_stage4()
            self._apply_late_data_policy()
            self._refresh_hop_counters_from_delta()
            recon = self.reconcile()
            self.stats.reconciliation = recon
            self.stats.alerts.extend(recon.alerts)
            if recon.within_tolerance:
                self.stats.success, self.stats.job_status = True, "complete"
            elif self.allow_partial:
                self.stats.success, self.stats.job_status = False, "partial_failed"
                logger.warning("Reconciliation failed; allow_partial=true")
            else:
                self.stats.success, self.stats.job_status = False, "failed"
                self.stats.end_time = datetime.now(timezone.utc)
                self._print_final_stats()
                raise ReconciliationError("Hop reconciliation failed; refusing pipeline success", context={"crawl_job_id": self.stats.crawl_job_id, "imbalance": recon.imbalance, "alerts": list(recon.alerts), "hop_funnel": self.stats.hop_funnel()})
            self.stats.end_time = datetime.now(timezone.utc)
            self._print_final_stats()
            return self.stats
        except Stage2BarrierError:
            self.stats.success, self.stats.job_status = False, "blocked"
            self.stats.end_time = datetime.now(timezone.utc)
            raise
        except ReconciliationError:
            raise
        except Exception as e:
            self.stats.success, self.stats.job_status = False, "failed"
            self.stats.end_time = datetime.now(timezone.utc)
            logger.error("Pipeline execution failed: %s", e)
            raise

    def run_stage_by_name(self, stage: Literal["stage1", "stage2", "stage3", "stage4"], **kwargs):
        if stage == "stage1": return self.run_stage1(**kwargs)
        if stage == "stage2": return asyncio.run(self.run_stage2(**kwargs))
        if stage == "stage3": return asyncio.run(self.run_stage3(**kwargs))
        if stage == "stage4": return asyncio.run(self.run_stage4(**kwargs))
        raise ValueError(f"Unknown stage: {stage}")

    def enforce_stage2_barrier(self) -> str:
        pending = self._count_stage2_pending()
        self.stats.stage2_pending_at_barrier = pending
        if pending > 0 and self.batch_mode:
            msg = f"Stage2 barrier: refusing Stage3/4 while {STAGE2_QUEUE_TABLE}.pending={pending}"
            self.stats.alerts.append(msg)
            logger.error(msg)
            raise Stage2BarrierError(msg, context={"pending": pending})
        watermark = datetime.now(timezone.utc).isoformat()
        self.stats.stage2_watermark = watermark
        logger.info("Stage2 barrier passed (pending=%s); watermark=%s", pending, watermark)
        return watermark

    def reconcile(self, *, stage2_write_failure: bool | None = None) -> ReconciliationResult:
        write_fail = self._detect_stage2_write_failure() if stage2_write_failure is None else stage2_write_failure
        result = reconcile_hops(
            self.stats.hops, crawl_job_id=self.stats.crawl_job_id or "unknown",
            tolerance=self.hop_reconciliation_tolerance, stage2_pending=self._count_stage2_pending(),
            stage2_watermark=self.stats.stage2_watermark, late_appends_flagged=self.stats.late_appends_flagged,
            late_data_policy=self.late_data_policy, stage2_write_failure=write_fail,
        )
        for alert in result.alerts:
            logger.warning("%s", alert)
        return result

    def get_hop_funnel(self) -> dict[str, Any]:
        return self.stats.hop_funnel()

    def _ensure_crawl_job_id(self) -> str:
        existing = self.config.get("crawl_job_id") or self.stats.crawl_job_id
        if existing: return str(existing)
        try:
            from src.otel_tracing import ensure_crawl_job_id
            return ensure_crawl_job_id()
        except Exception:
            return str(uuid.uuid4())

    def _count_stage2_pending(self) -> int:
        try:
            return len([i for i in (self.delta.read(STAGE2_QUEUE_TABLE) or []) if i.get("status") == "pending"])
        except Exception:
            return 0

    def _count_dlq(self) -> int:
        try: return len(self.delta.read(STAGE2_DLQ_TABLE) or [])
        except Exception: return 0

    def _refuse_if_stage2_pending(self, stage_name: str) -> None:
        if not self.stage2_barrier_enabled or not self.batch_mode: return
        pending = self._count_stage2_pending()
        if pending > 0:
            msg = f"Refusing {stage_name}: {STAGE2_QUEUE_TABLE}.pending={pending}"
            self.stats.alerts.append(msg)
            raise Stage2BarrierError(msg, context={"pending": pending, "stage": stage_name})

    def _detect_stage2_write_failure(self) -> bool:
        hops = self.stats.hops
        if hops.enqueued > 0 and hops.claimed == 0 and hops.ok == 0 and self._count_stage2_pending() == 0:
            return True
        return any("Stage2 write failure" in a for a in self.stats.alerts)

    def _apply_late_data_policy(self) -> None:
        if not self.stats.stage2_watermark: return
        late = self._count_stage2_pending()
        if late <= 0: return
        self.stats.late_appends_flagged = late
        self.stats.alerts.append(f"late_data: {late} post-watermark append(s) policy={self.late_data_policy}")
        logger.warning("late_data_policy=%s: %s post-watermark pending", self.late_data_policy, late)

    def _refresh_hop_counters_from_delta(self) -> None:
        try:
            queue = self.delta.read(STAGE2_QUEUE_TABLE) or []
            self.stats.hops.discovered = max(self.stats.hops.discovered, len(queue))
            self.stats.hops.enqueued = max(self.stats.hops.enqueued, len(queue))
            self.stats.hops.claimed = max(self.stats.hops.claimed, len([i for i in queue if i.get("status") != "pending"]))
        except Exception: pass
        try:
            analysis = self.delta.read(STAGE2_ANALYSIS_TABLE) or []
            failed = len([d for d in analysis if d.get("has_error")])
            self.stats.hops.ok = max(self.stats.hops.ok, len(analysis) - failed)
            self.stats.hops.failed = max(self.stats.hops.failed, failed)
        except Exception: pass
        self.stats.hops.dlq = max(self.stats.hops.dlq, self._count_dlq())

    def _print_final_stats(self) -> None:
        s = self.stats
        logger.info("PIPELINE COMPLETE success=%s status=%s hops=%s", s.success, s.job_status, s.hops.to_dict())
        for a in s.alerts: logger.info("alert: %s", a)

async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    await PipelineOrchestrator().run_full_pipeline(stage1_url_limit=50)

if __name__ == "__main__":
    asyncio.run(main())
