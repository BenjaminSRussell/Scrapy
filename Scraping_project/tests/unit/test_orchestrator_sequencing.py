"""PipelineOrchestrator sequencing (#256): stage order, failure stops downstream.

No Docker, Scrapy reactor, Kafka or real lake: stage 1 is patched and the stage
2/3/4 workers are recording fakes. Abort/partial-failure policy lives in
test_orchestrator_abort_684.py and test_orchestrator_run_status.py.
"""
from __future__ import annotations

import asyncio

import pytest

from src.orchestrator import pipeline_orchestrator as po


@pytest.fixture
def events():
    return []


@pytest.fixture
def orch(tmp_path, monkeypatch, events):
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))
    o = po.PipelineOrchestrator()

    def stage1(url_limit=None, spider_name="scout"):
        events.append(("stage1", "start"))
        events.append(("stage1", "end"))
        return 0

    def worker(name, result):
        class _W:
            def __init__(self, **kwargs):
                events.append((name, "init", tuple(sorted(kwargs))))

            async def run(self):
                events.append((name, "start"))
                await asyncio.sleep(0)
                events.append((name, "end"))
                return result

        return _W

    monkeypatch.setattr(o, "run_stage1", stage1)
    monkeypatch.setattr(po, "Stage2Worker", worker("stage2", {"analyzed": 4, "quality_docs": 3, "massive_docs": 1}))
    monkeypatch.setattr(po, "Stage3Worker", worker("stage3", 3))
    monkeypatch.setattr(po, "Stage4Worker", worker("stage4", 1))
    return o


def _order(events, phase):
    return [e[0] for e in events if e[1] == phase]


def test_happy_path_runs_stage1_then_2_then_3_and_4(orch, events):
    stats = asyncio.run(orch.run_full_pipeline(stage1_url_limit=5, stage2_concurrent=7, stage3_concurrent=2))
    assert stats.status == "complete"
    starts = _order(events, "start")
    ends = _order(events, "end")
    assert starts[:2] == ["stage1", "stage2"]
    assert set(starts[2:]) == {"stage3", "stage4"}
    # Stage 2 finishes before Stage 3/4 start; Stage 1 before Stage 2.
    assert ends.index("stage1") < starts.index("stage2")
    assert ends.index("stage2") < min(starts.index("stage3"), starts.index("stage4"))
    # Concurrency settings reach the workers.
    inits = {e[0]: e[2] for e in events if e[1] == "init"}
    assert "max_concurrent" in inits["stage2"] and "max_concurrent" in inits["stage3"]
    assert (stats.stage2_pages_analyzed, stats.stage3_summaries_created, stats.stage4_large_summaries) == (4, 3, 1)
    assert stats.end_time is not None


def test_stage1_failure_stops_everything_downstream(orch, events, monkeypatch):
    def boom(url_limit=None, spider_name="scout"):
        events.append(("stage1", "start"))
        raise RuntimeError("crawl failed")

    monkeypatch.setattr(orch, "run_stage1", boom)
    with pytest.raises(po.PipelineRunError) as exc:
        asyncio.run(orch.run_full_pipeline())
    assert exc.value.status == "failed"
    assert set(exc.value.stage_errors) == {"stage1"}
    assert _order(events, "start") == ["stage1"]  # no Stage 2/3/4 worker ever ran
    assert not [e for e in events if e[1] == "init"]


def test_stage2_failure_stops_stage3_and_stage4(orch, events, monkeypatch):
    class Boom:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            events.append(("stage2", "start"))
            raise RuntimeError("analysis failed")

    monkeypatch.setattr(po, "Stage2Worker", Boom)
    with pytest.raises(po.PipelineRunError) as exc:
        asyncio.run(orch.run_full_pipeline())
    assert exc.value.status == "failed"
    assert set(exc.value.stage_errors) == {"stage2"}
    assert "stage3" not in _order(events, "start") and "stage4" not in _order(events, "start")


@pytest.mark.parametrize("stage", ["stage2", "stage3", "stage4"])
def test_run_stage_by_name_runs_only_that_stage(orch, events, stage):
    orch.run_stage_by_name(stage)
    assert _order(events, "start") == [stage]


def test_run_stage_by_name_rejects_unknown(orch):
    with pytest.raises(ValueError, match="Unknown stage"):
        orch.run_stage_by_name("stage9")
