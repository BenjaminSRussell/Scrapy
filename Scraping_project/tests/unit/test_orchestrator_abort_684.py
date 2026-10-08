"""#684: an aborted pipeline run ends in a known terminal state.

Aborts (Ctrl-C, SystemExit from a signal handler, task cancellation) are injected
at each lifecycle phase with mocked stage workers. Each test asserts:
- status is ``aborted`` with ``aborted_stage`` and ``end_time`` set, and the metric
  is incremented;
- concurrent Stage 3/4 workers are cancelled and stopped within the test timeout;
- in-flight work is left un-acked (Stage 2 queue rows stay ``pending``);
- the next run on the same orchestrator starts clean (fresh stats, no stale state).
"""
from __future__ import annotations

import asyncio

import pytest

from src.lakehouse.lakehouse_manager import LakehouseManager
from src.orchestrator import pipeline_orchestrator as po

TIMEOUT = 10


def _metric(status):
    if po.PIPELINE_RUNS is None:
        return None
    return po.PIPELINE_RUNS.labels(status=status)._value.get()


class _OK2:
    def __init__(self, **kwargs):
        pass

    async def run(self):
        return {"analyzed": 3, "quality_docs": 2, "massive_docs": 1, "errors": 0}


class _OK3(_OK2):
    async def run(self):
        return 5


class _OK4(_OK2):
    async def run(self):
        return 7


class _Blocking:
    """Worker that runs until cancelled and records how it stopped."""

    started: asyncio.Event | None = None
    state = "new"

    def __init__(self, **kwargs):
        pass

    async def run(self):
        cls = type(self)
        cls.state = "running"
        if cls.started is not None:
            cls.started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cls.state = "cancelled"
            raise
        finally:
            if cls.state == "running":
                cls.state = "stopped"
        return 0


class _Blocking3(_Blocking):
    started = None
    state = "new"


class _Blocking4(_Blocking):
    started = None
    state = "new"


def _raiser(exc):
    class _W:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            raise exc

    return _W


@pytest.fixture
def orch(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))
    o = po.PipelineOrchestrator()
    monkeypatch.setattr(o, "run_stage1", lambda url_limit=None, spider_name="scout": 0)
    monkeypatch.setattr(po, "Stage2Worker", _OK2)
    monkeypatch.setattr(po, "Stage3Worker", _OK3)
    monkeypatch.setattr(po, "Stage4Worker", _OK4)
    for cls in (_Blocking3, _Blocking4):
        cls.state, cls.started = "new", None
    return o


def _assert_aborted(o, stage, exc_name):
    s = o.stats
    assert s.status == "aborted"
    assert s.aborted_stage == stage
    assert s.end_time is not None and s.start_time is not None and s.end_time >= s.start_time
    assert s.stage_errors.get(stage) == f"aborted: {exc_name}"
    assert o._current_stage is None


async def _clean_rerun(o, monkeypatch):
    monkeypatch.setattr(o, "run_stage1", lambda url_limit=None, spider_name="scout": 0)
    monkeypatch.setattr(po, "Stage2Worker", _OK2)
    monkeypatch.setattr(po, "Stage3Worker", _OK3)
    monkeypatch.setattr(po, "Stage4Worker", _OK4)
    stats = await asyncio.wait_for(o.run_full_pipeline(), timeout=TIMEOUT)
    assert stats.status == "complete" and stats.aborted_stage is None and stats.stage_errors == {}
    assert (stats.stage2_pages_analyzed, stats.stage3_summaries_created, stats.stage4_large_summaries) == (3, 5, 7)


# KeyboardInterrupt/SystemExit escape asyncio's task machinery (the loop re-raises
# them), so these run the pipeline the way an entry point does: asyncio.run().
@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit], ids=["ctrl-c", "sys-exit"])
def test_abort_during_startup_stage1(orch, monkeypatch, exc):
    before = _metric("aborted")

    def interrupted(url_limit=None, spider_name="scout"):
        raise exc()

    monkeypatch.setattr(orch, "run_stage1", interrupted)
    with pytest.raises(exc):
        asyncio.run(orch.run_full_pipeline())
    _assert_aborted(orch, "stage1", exc.__name__)
    if before is not None:
        assert _metric("aborted") == before + 1
    asyncio.run(_clean_rerun(orch, monkeypatch))


@pytest.mark.asyncio
async def test_cancel_during_active_stage2_leaves_queue_rows_pending(orch, monkeypatch, tmp_path):
    """Cancelling mid-Stage-2 must not ack anything; the rows are picked up next run."""
    from src.stage2.stage2_worker import Stage2Worker

    lake = LakehouseManager(base_path=str(tmp_path / "s2lake"), start_workers=False)
    rows = [{"url": f"https://www.uconn.edu/a/{i}", "url_hash": f"h{i}", "status": "pending"} for i in range(3)]
    assert lake._write_sync("stage2_queue", rows, "append")
    entered = asyncio.Event()

    class _HangingStage2(Stage2Worker):
        def __init__(self, **kwargs):
            super().__init__(max_concurrent=2, batch_size=10)
            self.delta = lake
            self.postgres = None

        async def _analyze_url(self, record):
            entered.set()
            await asyncio.sleep(3600)

    monkeypatch.setattr(po, "Stage2Worker", _HangingStage2)
    task = asyncio.ensure_future(orch.run_full_pipeline())
    await asyncio.wait_for(entered.wait(), timeout=TIMEOUT)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=TIMEOUT)
    _assert_aborted(orch, "stage2", "CancelledError")
    assert {r["status"] for r in lake.read("stage2_queue")} == {"pending"}
    lake.shutdown_event.set()
    await _clean_rerun(orch, monkeypatch)


@pytest.mark.asyncio
async def test_cancel_during_concurrent_stage3_stage4_stops_both_workers(orch, monkeypatch):
    _Blocking3.started, _Blocking4.started = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(po, "Stage3Worker", _Blocking3)
    monkeypatch.setattr(po, "Stage4Worker", _Blocking4)
    task = asyncio.ensure_future(orch.run_full_pipeline())
    await asyncio.wait_for(asyncio.gather(_Blocking3.started.wait(), _Blocking4.started.wait()), timeout=TIMEOUT)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=TIMEOUT)
    await asyncio.sleep(0)  # let cancelled children run their finally blocks
    assert (_Blocking3.state, _Blocking4.state) == ("cancelled", "cancelled")
    _assert_aborted(orch, "stage3+stage4", "CancelledError")
    # Counts from Stage 2 of the aborted run are still visible on its stats...
    assert orch.stats.stage2_pages_analyzed == 3
    await _clean_rerun(orch, monkeypatch)  # ...but never leak into the next run


@pytest.mark.asyncio
async def test_cancelled_stage_children_are_stage_failures_not_an_abort(orch, monkeypatch):
    """A stage's own child cancelled (not the run) is a stage failure, not an abort."""
    _Blocking3.started = asyncio.Event()
    monkeypatch.setattr(po, "Stage3Worker", _Blocking3)
    monkeypatch.setattr(po, "Stage4Worker", _raiser(asyncio.CancelledError()))

    async def stop_stage3_soon():
        await _Blocking3.started.wait()
        for t in asyncio.all_tasks():
            if t.get_coro().__qualname__.endswith("run_stage3"):
                t.cancel()

    helper = asyncio.ensure_future(stop_stage3_soon())
    with pytest.raises(po.PipelineRunError) as info:
        await asyncio.wait_for(orch.run_full_pipeline(), timeout=TIMEOUT)
    await helper
    assert info.value.status == "failed"
    assert orch.stats.status == "failed" and orch.stats.aborted_stage is None
    assert set(orch.stats.stage_errors) == {"stage3", "stage4"}


def test_abort_during_shutdown_after_all_stages(orch, monkeypatch):
    """An interrupt while the run is wrapping up (final stats) is still terminal."""

    def interrupted_print():
        raise KeyboardInterrupt()

    monkeypatch.setattr(orch, "_print_final_stats", interrupted_print)
    before = (_metric("complete"), _metric("aborted"))
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(orch.run_full_pipeline())
    # All stages finished and _finish("complete") ran before the interrupt: the
    # outcome stays "complete" (not relabelled, not double-counted), end_time is set.
    assert orch.stats.status == "complete" and orch.stats.aborted_stage is None
    assert orch.stats.end_time is not None and orch._current_stage is None
    if before[0] is not None:
        assert (_metric("complete"), _metric("aborted")) == (before[0] + 1, before[1])
    monkeypatch.setattr(orch, "_print_final_stats", lambda: None)
    asyncio.run(_clean_rerun(orch, monkeypatch))


@pytest.mark.asyncio
async def test_failure_is_still_failed_not_aborted(orch, monkeypatch):
    monkeypatch.setattr(po, "Stage2Worker", _raiser(RuntimeError("stage 2 broke")))
    with pytest.raises(po.PipelineRunError):
        await asyncio.wait_for(orch.run_full_pipeline(), timeout=TIMEOUT)
    assert orch.stats.status == "failed" and orch.stats.aborted_stage is None


def test_stats_reset_between_runs_even_without_abort(orch):
    orch.stats.stage2_pages_analyzed = 999
    orch.stats.stage_errors = {"stage3": "old"}
    stats = asyncio.run(orch.run_full_pipeline())
    assert stats.stage2_pages_analyzed == 3 and stats.stage_errors == {}
