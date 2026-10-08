"""#220: Stage 2 exports whether its queue was empty, how much was pending, or unreadable."""

import contextlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from prometheus_client import REGISTRY

from src.stage2.stage2_worker import Stage2Worker
from src.utils.delta import DeltaHelper, reset_delta

ROOT = Path(__file__).resolve().parents[3]


def _g(name):
    return REGISTRY.get_sample_value(name)


def _failures():
    return _g("stage2_queue_read_failures_total") or 0.0


@pytest.fixture
def helper_for(tmp_path):
    """DeltaHelper over a mocked LakehouseManager whose read_table returns/raises ``read``."""
    reset_delta()

    def make(read):
        helper = DeltaHelper(base_path=tmp_path / "delta_lake")
        manager = MagicMock()
        if isinstance(read, Exception):
            manager.read_table.side_effect = read
        else:
            manager.read_table.return_value = read
        helper._manager = manager
        return helper

    yield make
    reset_delta()


async def _run(delta):
    worker = Stage2Worker()
    worker.delta = delta
    return await worker._run_traced()


@pytest.mark.asyncio
async def test_empty_queue_sets_empty_gauge(helper_for):
    before = _failures()
    await _run(helper_for([]))
    assert _g("stage2_queue_empty") == 1.0
    assert _g("stage2_queue_pending") == 0.0
    assert _failures() == before


@pytest.mark.asyncio
async def test_only_completed_rows_counts_as_empty(helper_for):
    await _run(helper_for([{"url": "https://uconn.edu/a", "status": "completed"}]))
    assert _g("stage2_queue_empty") == 1.0
    assert _g("stage2_queue_pending") == 0.0


@pytest.mark.asyncio
async def test_pending_rows_clear_empty_gauge(monkeypatch, helper_for):
    rows = [{"url": f"https://uconn.edu/{i}", "url_hash": str(i), "status": "pending"} for i in range(3)]
    monkeypatch.setattr(Stage2Worker, "_load_prior_failures", lambda self: {})

    async def fake_analyze(self, record):
        return {"url": record["url"], "error": "skipped in test"}

    monkeypatch.setattr(Stage2Worker, "_analyze_url", fake_analyze)
    delta = helper_for(rows)
    # Batch bookkeeping against the MagicMock manager is not under test; the
    # gauges are set right after the queue read, before any of it.
    with contextlib.suppress(Exception):
        await _run(delta)
    assert _g("stage2_queue_empty") == 0.0
    assert _g("stage2_queue_pending") == 3.0


@pytest.mark.asyncio
async def test_unreadable_queue_is_counted_not_reported_empty(caplog, helper_for):
    await _run(helper_for([]))  # gauge starts at "empty"
    before = _failures()
    pending_before = _g("stage2_queue_pending")
    await _run(helper_for(OSError("s3: access denied")))
    assert _failures() == before + 1
    assert _g("stage2_queue_pending") == pending_before  # unknown, not overwritten with 0
    assert "Could not read stage2_queue" in caplog.text


@pytest.mark.asyncio
async def test_raw_manager_raising_is_also_counted():
    delta = MagicMock()
    delta.read_table.side_effect = RuntimeError("boom")
    before = _failures()
    await _run(delta)
    assert _failures() == before + 1


def test_delta_helper_keeps_last_read_error(helper_for):
    helper = helper_for(OSError("nope"))
    assert helper.read_table("stage2_queue") == []
    assert isinstance(helper.last_read_error, OSError)
    helper.manager.read_table.side_effect = None
    helper.manager.read_table.return_value = [{"a": 1}]
    assert helper.read_table("stage2_queue") == [{"a": 1}]
    assert helper.last_read_error is None


@pytest.mark.parametrize(
    "path", ["monitoring/alerting_rules.yml", "k8s/helm/scraping-pipeline/files/monitoring/alerting_rules.yml"]
)
def test_alert_rules_present_in_both_copies(path):
    groups = yaml.safe_load((ROOT / path).read_text())["groups"]
    rules = {r.get("alert"): r for g in groups for r in g["rules"]}
    assert "stage2_queue_empty" in rules["Stage2StarvedWhileStage1Active"]["expr"]
    assert "stage2_queue_read_failures_total" in rules["Stage2QueueUnreadable"]["expr"]
