"""Hop funnel accounting and reconciliation for pipeline runs (#646).

Tracks per-crawl_job_id hop counters (discovered → enqueued → claimed →
ok/failed/dlq) and evaluates whether |in−out| stays within tolerance.
Also provides a minimal late-data hook (#310 full policy is out of scope).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class HopCounters:
    """End-to-end hop counters for a single crawl_job_id."""

    discovered: int = 0
    enqueued: int = 0
    claimed: int = 0
    ok: int = 0
    failed: int = 0
    dlq: int = 0

    @property
    def terminal(self) -> int:
        """Items that reached a terminal hop (ok + failed + dlq)."""
        return self.ok + self.failed + self.dlq

    @property
    def imbalance(self) -> int:
        """Largest absolute gap across adjacent funnel hops."""
        gaps = [
            abs(self.discovered - self.enqueued),
            abs(self.enqueued - self.claimed),
            abs(self.claimed - self.terminal),
        ]
        return max(gaps) if gaps else 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class ReconciliationResult:
    """Outcome of hop reconciliation for a crawl job."""

    crawl_job_id: str
    hops: HopCounters
    tolerance: int
    within_tolerance: bool
    imbalance: int
    alerts: list[str] = field(default_factory=list)
    stage2_pending: int = 0
    stage2_watermark: str | None = None
    late_appends_flagged: int = 0
    late_data_policy: str = "flag"

    def to_dict(self) -> dict[str, Any]:
        return {
            "crawl_job_id": self.crawl_job_id,
            "hops": self.hops.to_dict(),
            "tolerance": self.tolerance,
            "within_tolerance": self.within_tolerance,
            "imbalance": self.imbalance,
            "alerts": list(self.alerts),
            "stage2_pending": self.stage2_pending,
            "stage2_watermark": self.stage2_watermark,
            "late_appends_flagged": self.late_appends_flagged,
            "late_data_policy": self.late_data_policy,
        }


def reconcile_hops(
    hops: HopCounters,
    *,
    crawl_job_id: str,
    tolerance: int = 0,
    stage2_pending: int = 0,
    stage2_watermark: str | None = None,
    late_appends_flagged: int = 0,
    late_data_policy: str = "flag",
    stage2_write_failure: bool = False,
) -> ReconciliationResult:
    """Reconcile hop counters and emit alerts when the funnel is inconsistent.

    Args:
        hops: Funnel counters for this job.
        crawl_job_id: Correlation id for the crawl.
        tolerance: Allowed |in−out| before alerting / failing.
        stage2_pending: Pending rows still in stage2_queue.
        stage2_watermark: ISO timestamp when Stage2→3/4 barrier passed.
        late_appends_flagged: Count of post-watermark appends (minimal #310 hook).
        late_data_policy: ``flag`` | ``process`` | ``quarantine`` (#310 stub).
        stage2_write_failure: Explicit / injected Stage2 write failure signal.

    Returns:
        ReconciliationResult with alerts and within_tolerance flag.
    """
    alerts: list[str] = []
    imbalance = hops.imbalance

    if stage2_write_failure:
        alerts.append(
            "hop_alert: Stage2 write failure detected "
            f"(enqueued={hops.enqueued}, claimed={hops.claimed}, ok={hops.ok})"
        )
        imbalance = max(imbalance, abs(hops.enqueued - hops.ok))

    if hops.discovered > 0 and hops.enqueued == 0:
        alerts.append(
            f"hop_alert: discovered={hops.discovered} but enqueued=0 "
            "(silent enqueue / Stage2 queue write loss)"
        )

    if hops.enqueued > 0 and hops.claimed == 0 and stage2_pending == 0:
        alerts.append(
            f"hop_alert: enqueued={hops.enqueued} but claimed=0 with empty pending "
            "(possible Stage2 write/claim failure)"
        )

    if abs(hops.claimed - hops.terminal) > tolerance:
        alerts.append(
            f"hop_alert: claimed={hops.claimed} vs terminal={hops.terminal} "
            f"(ok={hops.ok}, failed={hops.failed}, dlq={hops.dlq}) "
            f"exceeds tolerance={tolerance}"
        )

    if abs(hops.enqueued - hops.claimed) > tolerance and stage2_pending == 0:
        alerts.append(
            f"hop_alert: enqueued={hops.enqueued} vs claimed={hops.claimed} "
            f"exceeds tolerance={tolerance}"
        )

    if stage2_pending > 0:
        alerts.append(
            f"hop_alert: stage2_queue.pending={stage2_pending} at reconciliation"
        )

    if late_appends_flagged > 0:
        alerts.append(
            f"late_data: flagged {late_appends_flagged} post-watermark append(s) "
            f"(policy={late_data_policy}; full #310 out of scope)"
        )

    within = True
    if stage2_write_failure:
        within = False
    elif imbalance > tolerance:
        within = False
    elif hops.enqueued > 0 and hops.claimed == 0 and stage2_pending == 0:
        within = False
    elif stage2_pending > 0:
        within = False
    elif abs(hops.claimed - hops.terminal) > tolerance:
        within = False

    return ReconciliationResult(
        crawl_job_id=crawl_job_id,
        hops=hops,
        tolerance=tolerance,
        within_tolerance=within,
        imbalance=imbalance,
        alerts=alerts,
        stage2_pending=stage2_pending,
        stage2_watermark=stage2_watermark,
        late_appends_flagged=late_appends_flagged,
        late_data_policy=late_data_policy,
    )


def hop_funnel_panel(result: ReconciliationResult) -> dict[str, Any]:
    """Structured hop-funnel payload for dashboards / PipelineStats consumers."""
    return {
        "panel": "hop_funnel",
        "crawl_job_id": result.crawl_job_id,
        "funnel": result.hops.to_dict(),
        "imbalance": result.imbalance,
        "tolerance": result.tolerance,
        "within_tolerance": result.within_tolerance,
        "alerts": list(result.alerts),
        "stage2_pending": result.stage2_pending,
        "stage2_watermark": result.stage2_watermark,
        "late_appends_flagged": result.late_appends_flagged,
        "late_data_policy": result.late_data_policy,
    }
