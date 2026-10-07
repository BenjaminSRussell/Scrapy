"""#521: partial stage failure is never reported as a successful run."""

import asyncio

import pytest

from src.orchestrator import pipeline_orchestrator as po


class _OK2:
    def __init__(self, **kwargs):
        pass

    async def run(self):
        return {"analyzed": 1, "quality_docs": 1, "massive_docs": 0, "errors": 0}


class _OK3(_OK2):
    async def run(self):
        return 1


class _Boom:
    def __init__(self, **kwargs):
        pass

    async def run(self):
        raise RuntimeError("injected failure")


class _SlowOK4:
    """Stage 4 must still be awaited to completion when Stage 3 fails."""

    finished = False

    def __init__(self, **kwargs):
        pass

    async def run(self):
        await asyncio.sleep(0.01)
        type(self).finished = True
        return 2


@pytest.fixture
def orch(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))
    o = po.PipelineOrchestrator()
    monkeypatch.setattr(o, "run_stage1", lambda url_limit=None, spider_name="scout": 0)
    monkeypatch.setattr(po, "Stage2Worker", _OK2)
    monkeypatch.setattr(po, "Stage3Worker", _OK3)
    monkeypatch.setattr(po, "Stage4Worker", _SlowOK4)
    _SlowOK4.finished = False
    return o


def _metric(status):
    if po.PIPELINE_RUNS is None:
        return None
    return po.PIPELINE_RUNS.labels(status=status)._value.get()


def test_all_stages_ok_is_complete(orch):
    stats = asyncio.run(orch.run_full_pipeline())
    assert stats.status == "complete"
    assert stats.stage_errors == {}


def test_injected_stage3_failure_is_partial_and_raises(orch, monkeypatch):
    monkeypatch.setattr(po, "Stage3Worker", _Boom)
    before = _metric("partial_failed")

    with pytest.raises(po.PipelineRunError) as exc:
        asyncio.run(orch.run_full_pipeline())

    assert exc.value.status == "partial_failed"
    assert "stage3" in exc.value.stage_errors
    assert orch.stats.status == "partial_failed"
    assert _SlowOK4.finished, "Stage 4 was orphaned instead of awaited"
    assert orch.stats.stage4_large_summaries == 2
    if before is not None:
        assert _metric("partial_failed") == before + 1


def test_allow_partial_returns_partial_status(orch, monkeypatch):
    monkeypatch.setattr(po, "Stage3Worker", _Boom)
    stats = asyncio.run(orch.run_full_pipeline(allow_partial=True))
    assert stats.status == "partial_failed"
    assert set(stats.stage_errors) == {"stage3"}


def test_both_parallel_stages_failing_is_failed_even_with_allow_partial(orch, monkeypatch):
    monkeypatch.setattr(po, "Stage3Worker", _Boom)
    monkeypatch.setattr(po, "Stage4Worker", _Boom)
    with pytest.raises(po.PipelineRunError) as exc:
        asyncio.run(orch.run_full_pipeline(allow_partial=True))
    assert exc.value.status == "failed"


def test_required_stage2_failure_stops_the_run(orch, monkeypatch):
    monkeypatch.setattr(po, "Stage2Worker", _Boom)
    ran = []

    class _Track3(_OK3):
        async def run(self):
            ran.append("stage3")
            return 1

    monkeypatch.setattr(po, "Stage3Worker", _Track3)
    with pytest.raises(po.PipelineRunError) as exc:
        asyncio.run(orch.run_full_pipeline(allow_partial=True))
    assert exc.value.status == "failed"
    assert set(exc.value.stage_errors) == {"stage2"}
    assert ran == []
    assert orch.stats.end_time is not None
