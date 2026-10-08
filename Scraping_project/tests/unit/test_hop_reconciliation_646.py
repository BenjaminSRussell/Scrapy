"""#646: stage2_queue hop reconciliation and the Stage 2 -> 3/4 barrier."""

from __future__ import annotations

import asyncio

import pytest

from src.orchestrator import pipeline_orchestrator as po
from src.orchestrator.hop_reconciliation import count_hops, reconcile, url_statuses


def _rows(*pairs):
    return [{"url": f"https://x.example/{k}", "url_hash": k, "status": s} for k, s in pairs]


# --- pure accounting -------------------------------------------------------


def test_count_hops_per_url():
    before = _rows(("a", "pending"), ("b", "pending"), ("c", "pending"), ("d", "pending"), ("old", "completed"))
    after = _rows(("a", "completed"), ("b", "failed"), ("d", "pending"), ("old", "completed"), ("new", "pending"))
    hops = count_hops(before, after, discovered=9)
    assert hops.to_dict() == {
        "discovered": 9, "enqueued": 4, "claimed": 3, "ok": 1, "failed": 1,
        "lost": 1, "still_pending": 1, "late_appends": 1,
    }
    assert hops.terminal == 2


def test_duplicate_rows_take_the_most_final_status():
    assert url_statuses(_rows(("a", "pending"), ("a", "completed"), ("b", "failed"), ("b", "pending"))) == {
        "a": "completed", "b": "failed",
    }


def test_reconcile_fails_only_on_loss_beyond_tolerance():
    lossy = count_hops(_rows(("a", "pending"), ("b", "pending")), _rows(("a", "completed")))
    result = reconcile(lossy)
    assert not result.within_tolerance
    assert [a.split(":")[0] for a in result.alerts] == ["hop_lost"]
    assert reconcile(lossy, tolerance=1).within_tolerance

    pending = count_hops(_rows(("a", "pending")), _rows(("a", "pending"), ("n", "pending")))
    result = reconcile(pending)
    assert result.within_tolerance
    assert [a.split(":")[0] for a in result.alerts] == ["stage2_pending", "late_append"]
    panel = result.panel("job-1")
    assert panel["panel"] == "hop_funnel" and panel["crawl_job_id"] == "job-1"
    assert panel["funnel"]["still_pending"] == 1


# --- orchestrator ----------------------------------------------------------


class _Lake:
    def __init__(self, rows):
        self.tables = {"stage2_queue": [dict(r) for r in rows]}

    def read(self, name, *a, **k):
        return [dict(r) for r in self.tables.get(name, [])]


class _OK34:
    started: list = []

    def __init__(self, **kwargs):
        pass

    async def run(self):
        type(self).started.append(type(self).__name__)
        return 1


class _S3(_OK34):
    pass


class _S4(_OK34):
    pass


def _stage2(action):
    class _W:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            action(LAKE["lake"].tables["stage2_queue"])
            return {"analyzed": 2, "quality_docs": 2, "massive_docs": 0, "errors": 0}

    return _W


LAKE: dict = {}


def _complete_all(rows):
    for r in rows:
        r["status"] = "completed"


@pytest.fixture
def orch(monkeypatch):
    for env in ("STAGE2_BARRIER", "HOP_TOLERANCE", "STAGE3_4_PARALLEL", "ENABLE_JS_SPIDER"):
        monkeypatch.delenv(env, raising=False)
    lake = _Lake(_rows(("a", "pending"), ("b", "pending")))
    LAKE["lake"] = lake
    monkeypatch.setattr(po, "get_delta", lambda: lake)
    o = po.PipelineOrchestrator()
    monkeypatch.setattr(o, "run_stage1", lambda url_limit=None, spider_name="scout": 0)
    monkeypatch.setattr(o, "run_js_queue", lambda: 0)
    monkeypatch.setattr(po, "Stage3Worker", _S3)
    monkeypatch.setattr(po, "Stage4Worker", _S4)
    _OK34.started = []
    return o


def _run(o, **kw):
    return asyncio.run(o.run_full_pipeline(**kw))


def _alerts(kind):
    if po.PIPELINE_HOP_ALERTS is None:
        return None
    return po.PIPELINE_HOP_ALERTS.labels(alert=kind)._value.get()


def test_balanced_run_completes_with_funnel_and_watermark(orch, monkeypatch):
    monkeypatch.setattr(po, "Stage2Worker", _stage2(_complete_all))
    stats = _run(orch)
    assert stats.status == "complete"
    assert stats.hop_funnel["funnel"]["ok"] == 2 and stats.hop_funnel["within_tolerance"]
    assert stats.stage2_watermark is not None
    assert "reconciliation" not in stats.stage_errors


def test_vanished_rows_are_never_success(orch, monkeypatch):
    def drop_one(rows):
        _complete_all(rows)
        del rows[1]

    monkeypatch.setattr(po, "Stage2Worker", _stage2(drop_one))
    before = _alerts("hop_lost")
    with pytest.raises(po.PipelineRunError) as info:
        _run(orch)
    assert info.value.status == "partial_failed"
    assert "vanished" in orch.stats.stage_errors["reconciliation"]
    if before is not None:
        assert _alerts("hop_lost") == before + 1

    LAKE["lake"].tables["stage2_queue"] = _rows(("a", "pending"), ("b", "pending"))
    stats = _run(orch, allow_partial=True)
    assert stats.status == "partial_failed"


def test_tolerance_absorbs_small_gaps(orch, monkeypatch):
    monkeypatch.setenv("HOP_TOLERANCE", "1")
    monkeypatch.setattr(po, "Stage2Worker", _stage2(lambda rows: (_complete_all(rows), rows.pop())))
    assert _run(orch).status == "complete"


def test_stage2_status_write_failure_is_flagged(orch, monkeypatch):
    """Rows Stage 2 analysed but could not ack stay pending: alert, run continues."""
    monkeypatch.setattr(po, "Stage2Worker", _stage2(lambda rows: rows[0].update(status="completed")))
    before = _alerts("stage2_pending")
    stats = _run(orch)
    assert stats.status == "complete"
    assert stats.hop_funnel["funnel"]["still_pending"] == 1
    assert any(a.startswith("stage2_pending") for a in stats.hop_funnel["alerts"])
    if before is not None:
        assert _alerts("stage2_pending") == before + 1


def test_strict_barrier_refuses_stage3_and_stage4(orch, monkeypatch):
    monkeypatch.setenv("STAGE2_BARRIER", "strict")
    monkeypatch.setattr(po, "Stage2Worker", _stage2(lambda rows: rows[0].update(status="completed")))
    with pytest.raises(po.PipelineRunError) as info:
        _run(orch)
    assert info.value.status == "failed"
    assert "stage2_barrier" in orch.stats.stage_errors
    assert orch.stats.stage2_watermark is None
    assert _OK34.started == []


def test_late_append_is_flagged_then_processed_next_run(orch, monkeypatch):
    def complete_and_append(rows):
        _complete_all(rows)
        rows.extend(_rows(("late", "pending")))

    monkeypatch.setattr(po, "Stage2Worker", _stage2(complete_and_append))
    stats = _run(orch)
    assert stats.status == "complete"
    assert stats.hop_funnel["funnel"]["late_appends"] == 1

    monkeypatch.setattr(po, "Stage2Worker", _stage2(_complete_all))
    stats = _run(orch)
    assert stats.hop_funnel["funnel"]["enqueued"] == 1 and stats.hop_funnel["funnel"]["ok"] == 1
    assert stats.hop_funnel["funnel"]["late_appends"] == 0


def test_barrier_off_skips_accounting(orch, monkeypatch):
    monkeypatch.setenv("STAGE2_BARRIER", "off")
    monkeypatch.setattr(po, "Stage2Worker", _stage2(lambda rows: rows.clear()))
    stats = _run(orch)
    assert stats.status == "complete" and stats.hop_funnel is None


def test_unreadable_queue_skips_accounting(orch, monkeypatch):
    def broken(name, *a, **k):
        raise OSError("lake down")

    monkeypatch.setattr(orch.delta, "read", broken)
    monkeypatch.setattr(po, "Stage2Worker", _stage2(lambda rows: None))
    assert _run(orch).hop_funnel is None


def test_sequential_mode_runs_stage4_after_stage3_even_if_it_fails(orch, monkeypatch):
    monkeypatch.setenv("STAGE3_4_PARALLEL", "false")
    monkeypatch.setattr(po, "Stage2Worker", _stage2(_complete_all))

    class _Boom3(_OK34):
        async def run(self):
            type(self).started.append("_Boom3")
            raise RuntimeError("stage3 down")

    monkeypatch.setattr(po, "Stage3Worker", _Boom3)
    stats = _run(orch, allow_partial=True)
    assert _OK34.started == ["_Boom3", "_S4"]
    assert stats.status == "partial_failed" and set(stats.stage_errors) == {"stage3"}
