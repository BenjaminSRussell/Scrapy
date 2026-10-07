"""#327: PipelineStats reflect per-run worker results after run_full_pipeline."""

import asyncio

import pytest

from src.orchestrator import pipeline_orchestrator as po


class _FakeStage2:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def run(self):
        return {"analyzed": 7, "quality_docs": 4, "massive_docs": 2, "errors": 1}


class _FakeStage3:
    def __init__(self, **kwargs):
        pass

    async def run(self):
        return 4


class _FakeStage4:
    def __init__(self, **kwargs):
        pass

    async def run(self):
        return 2


class _LegacyWorker:
    """A worker whose run() returns None (pre-#327 contract)."""

    def __init__(self, **kwargs):
        pass

    async def run(self):
        return None


class _FakeDelta:
    def __init__(self, tables):
        self.tables = tables

    def read(self, name, **kwargs):
        return list(self.tables.get(name, []))


@pytest.fixture
def orchestrator(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))
    orch = po.PipelineOrchestrator()
    # Cumulative tables hold rows from earlier runs: stats must NOT use them when
    # the worker reports per-run counts.
    orch.delta = _FakeDelta({"stage2_page_analysis": [{}] * 100, "stage3_summaries": [{}] * 50})
    return orch


def test_full_pipeline_stats_come_from_worker_results(orchestrator, monkeypatch):
    monkeypatch.setattr(po, "Stage2Worker", _FakeStage2)
    monkeypatch.setattr(po, "Stage3Worker", _FakeStage3)
    monkeypatch.setattr(po, "Stage4Worker", _FakeStage4)

    def fake_stage1(url_limit=None, spider_name="scout"):
        orchestrator.stats.stage1_urls_queued = 7
        return 7

    monkeypatch.setattr(orchestrator, "run_stage1", fake_stage1)

    asyncio.run(orchestrator.run_full_pipeline(stage1_url_limit=5))

    stats = orchestrator.stats
    assert stats.stage1_urls_queued == 7
    assert stats.stage2_pages_analyzed == 7
    assert stats.stage2_quality_docs == 4
    assert stats.stage2_massive_docs == 2
    assert stats.stage3_summaries_created == 4
    assert stats.stage4_large_summaries == 2
    assert stats.end_time is not None and stats.total_duration_seconds >= 0


def test_zero_only_when_nothing_processed(orchestrator, monkeypatch):
    class _Idle2(_FakeStage2):
        async def run(self):
            return {"analyzed": 0, "quality_docs": 0, "massive_docs": 0, "errors": 0}

    monkeypatch.setattr(po, "Stage2Worker", _Idle2)
    assert asyncio.run(orchestrator.run_stage2()) == 0
    assert orchestrator.stats.stage2_pages_analyzed == 0


def test_legacy_worker_falls_back_to_table_counts(orchestrator, monkeypatch):
    monkeypatch.setattr(po, "Stage3Worker", _LegacyWorker)
    assert asyncio.run(orchestrator.run_stage3()) == 50
    assert orchestrator.stats.stage3_summaries_created == 50


def test_is_count_rejects_bools_and_mocks():
    from unittest.mock import MagicMock

    assert po._is_count(3)
    assert not po._is_count(True)
    assert not po._is_count(MagicMock())
    assert not po._is_count(None)
