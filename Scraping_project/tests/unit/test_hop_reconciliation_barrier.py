"""Tests for hop reconciliation + Stage2 barrier (#646)."""
from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from src.orchestrator.hop_reconciliation import HopCounters, hop_funnel_panel, reconcile_hops
from src.orchestrator.pipeline_orchestrator import (
    PipelineOrchestrator, PipelineStats, ReconciliationError, Stage2BarrierError,
)

def test_reconcile_balanced_passes():
    hops = HopCounters(discovered=10, enqueued=10, claimed=10, ok=8, failed=1, dlq=1)
    result = reconcile_hops(hops, crawl_job_id="j", tolerance=0)
    assert result.within_tolerance and result.imbalance == 0 and result.alerts == []

def test_stage2_write_failure_trips_alert():
    hops = HopCounters(discovered=5, enqueued=5, claimed=0, ok=0)
    result = reconcile_hops(hops, crawl_job_id="j", stage2_write_failure=True)
    assert not result.within_tolerance
    assert any("Stage2 write failure" in a for a in result.alerts)
    panel = hop_funnel_panel(result)
    assert panel["panel"] == "hop_funnel" and panel["funnel"]["enqueued"] == 5

def test_imbalance_beyond_tolerance_fails():
    hops = HopCounters(discovered=100, enqueued=100, claimed=100, ok=50)
    assert reconcile_hops(hops, crawl_job_id="j", tolerance=0).within_tolerance is False

def test_late_appends_flagged():
    hops = HopCounters(discovered=3, enqueued=3, claimed=3, ok=3)
    result = reconcile_hops(hops, crawl_job_id="j", late_appends_flagged=2, late_data_policy="flag")
    assert result.late_appends_flagged == 2
    assert any("late_data" in a for a in result.alerts)

def _orch(config=None):
    with patch("src.orchestrator.pipeline_orchestrator.get_delta") as gd:
        delta = MagicMock()
        gd.return_value = delta
        o = PipelineOrchestrator(config=config or {})
        o.delta = delta
        return o

def test_defaults():
    o = _orch()
    assert o.stage3_4_parallel is False and o.allow_partial is False
    assert o.stage2_barrier_enabled is True and o.batch_mode is True

def test_barrier_refuses_when_pending():
    o = _orch({"batch_mode": True})
    o.delta.read.return_value = [{"url": "a", "status": "pending"}, {"url": "b", "status": "pending"}]
    with pytest.raises(Stage2BarrierError):
        o.enforce_stage2_barrier()
    assert o.stats.stage2_pending_at_barrier == 2

def test_barrier_sets_watermark_when_empty():
    o = _orch()
    o.delta.read.return_value = [{"url": "a", "status": "completed"}]
    wm = o.enforce_stage2_barrier()
    assert wm and o.stats.stage2_watermark == wm and o.stats.stage2_pending_at_barrier == 0

def test_refuse_stage3_while_pending():
    o = _orch()
    o.delta.read.return_value = [{"url": "a", "status": "pending"}]
    async def _run():
        with pytest.raises(Stage2BarrierError):
            await o.run_stage3()
    asyncio.run(_run())

@pytest.mark.asyncio
async def test_reconciliation_blocks_success():
    o = _orch({"allow_partial": False, "stage3_4_parallel": False})
    o.delta.read.return_value = []
    async def fail_s2(**kw):
        o.stats.hops = HopCounters(discovered=5, enqueued=5, claimed=0, ok=0)
        o.stats.alerts.append("hop_alert: Stage2 write failure detected")
        return 0
    with patch.object(o, "run_stage1", return_value=5), patch.object(o, "run_stage2", AsyncMock(side_effect=fail_s2)), patch.object(o, "run_stage3", AsyncMock(return_value=0)), patch.object(o, "run_stage4", AsyncMock(return_value=0)), patch.object(o, "_detect_stage2_write_failure", return_value=True):
        with pytest.raises(ReconciliationError):
            await o.run_full_pipeline(stage1_url_limit=5)
    assert o.stats.success is False and o.stats.job_status == "failed"
    funnel = o.get_hop_funnel()
    assert funnel["panel"] == "hop_funnel" and funnel["success"] is False

@pytest.mark.asyncio
async def test_allow_partial_not_success():
    o = _orch({"allow_partial": True, "stage3_4_parallel": False})
    o.delta.read.return_value = []
    async def fail_s2(**kw):
        o.stats.hops = HopCounters(discovered=4, enqueued=4, claimed=0, ok=0)
        return 0
    with patch.object(o, "run_stage1", return_value=4), patch.object(o, "run_stage2", AsyncMock(side_effect=fail_s2)), patch.object(o, "run_stage3", AsyncMock(return_value=0)), patch.object(o, "run_stage4", AsyncMock(return_value=0)), patch.object(o, "_detect_stage2_write_failure", return_value=True):
        stats = await o.run_full_pipeline(stage1_url_limit=4)
    assert stats.success is False and stats.job_status == "partial_failed"
    assert stats.reconciliation and stats.reconciliation.within_tolerance is False

@pytest.mark.asyncio
async def test_late_append_flagged_after_watermark():
    o = _orch({"allow_partial": True, "late_data_policy": "flag", "stage3_4_parallel": False})
    state = {"n": 0}
    def delta_read(table):
        if table == "stage2_queue":
            return [{"url": "d", "status": "completed"}] if state["n"] == 0 else [{"url": "d", "status": "completed"}, {"url": "late", "status": "pending"}]
        return []
    o.delta.read.side_effect = delta_read
    async def s2(**kw):
        o.stats.hops = HopCounters(discovered=1, enqueued=1, claimed=1, ok=1)
        return 1
    async def s3(**kw):
        state["n"] = 1
        return 0
    with patch.object(o, "run_stage1", return_value=1), patch.object(o, "run_stage2", AsyncMock(side_effect=s2)), patch.object(o, "run_stage3", AsyncMock(side_effect=s3)), patch.object(o, "run_stage4", AsyncMock(return_value=0)):
        stats = await o.run_full_pipeline(stage1_url_limit=1)
    assert stats.late_appends_flagged >= 1
    assert any("late_data" in a for a in stats.alerts)
    assert stats.stage2_watermark is not None

@pytest.mark.asyncio
async def test_balanced_pipeline_success():
    o = _orch({"allow_partial": False, "stage3_4_parallel": False, "batch_mode": True})
    o.delta.read.return_value = [{"url": "x", "status": "completed"}]
    async def s2(**kw):
        o.stats.hops = HopCounters(discovered=2, enqueued=2, claimed=2, ok=2)
        return 2
    with patch.object(o, "run_stage1", return_value=2), patch.object(o, "run_stage2", AsyncMock(side_effect=s2)), patch.object(o, "run_stage3", AsyncMock(return_value=1)), patch.object(o, "run_stage4", AsyncMock(return_value=0)):
        stats = await o.run_full_pipeline(stage1_url_limit=2)
    assert stats.success is True and stats.job_status == "complete"
    assert stats.reconciliation and stats.reconciliation.within_tolerance
    assert stats.hop_funnel()["funnel"]["ok"] == 2

def test_hop_funnel_without_reconciliation():
    stats = PipelineStats(crawl_job_id="x", hops=HopCounters(discovered=1, enqueued=1))
    panel = stats.hop_funnel()
    assert panel["panel"] == "hop_funnel" and panel["crawl_job_id"] == "x"
