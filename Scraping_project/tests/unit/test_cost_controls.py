"""Per-stage daily budgets for Stage 3 GPU minutes and Stage 4 GPU/OCR (#528).

Missing or null caps do not stop a worker. A numeric cap stops the batch with
status budget_exhausted before the next GPU, OCR, network, or paid call.
The clock is injectable; these tests use a fake clock and a tiny cap.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from prometheus_client import REGISTRY

from src.stage3.stage3_worker import FingerprintStore, Stage3Worker
from src.utils import cost_controls
from src.utils.cost_controls import (
    OCR_PAGES,
    STAGE3_GPU_MINUTES_KEY,
    STAGE4_GPU_MINUTES_KEY,
    STAGE4_OCR_PAGES_KEY,
    STATUS_BUDGET_EXHAUSTED,
    CostBudget,
    ocr_pages_for,
)
from src.utils.graceful_shutdown import reset_for_tests
from tests.unit.stage4.test_stage4_worker import FakeDelta, FakeProcessor, Recorder, _row, _worker

pytestmark = pytest.mark.unit

ROOT_CONFIG_PARENTS = 2


class FakeClock:
    def __init__(self, now: datetime) -> None:
        if now.tzinfo is None:
            raise ValueError("fake clock must be timezone-aware")
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, *, seconds: float = 0, days: float = 0) -> None:
        self._now = self._now + timedelta(seconds=seconds, days=days)


def _gauge(stage: str, resource: str) -> float | None:
    return REGISTRY.get_sample_value("budget_remaining", {"stage": stage, "resource": resource})


def _budget(clock: FakeClock, **caps: Any) -> CostBudget:
    return CostBudget(clock=clock.now, caps=caps, ledger={})


def _stage3_doc(url: str, text: str) -> dict[str, Any]:
    return {
        "url": url,
        "url_hash": f"h-{url}",
        "text_content": text,
        "is_low_quality": False,
        "is_massive_doc": False,
        "has_error": False,
        "keywords": ["research"],
        "quality_score": 80,
    }


class _Stage3Delta:
    def __init__(self) -> None:
        self.tables: dict[str, list] = {}
        self.writes: list[tuple[str, list]] = []

    def read(self, table: str, **_kw: Any) -> list:
        if table not in self.tables:
            raise FileNotFoundError(table)
        return list(self.tables[table])

    def write(self, table: str, rows: list, **_kw: Any) -> bool:
        self.writes.append((table, list(rows)))
        self.tables.setdefault(table, []).extend(rows)
        return True


@pytest.fixture
def stage3(monkeypatch: pytest.MonkeyPatch) -> Stage3Worker:
    worker = Stage3Worker(max_concurrent=2, batch_size=10)
    worker.delta = _Stage3Delta()  # type: ignore[assignment]
    worker.postgres = MagicMock(name="postgres")
    worker._fingerprints = FingerprintStore(worker.SIMILARITY_THRESHOLD, redis_client=None)
    monkeypatch.setattr("src.stage3.stage3_worker.init_tracing", lambda **_k: None)
    monkeypatch.setattr("src.stage3.stage3_worker.ensure_crawl_job_id", lambda: "job-test")
    monkeypatch.setattr("src.stage3.stage3_worker.start_span", MagicMock())
    monkeypatch.setattr("src.stage3.stage3_worker.record_performance", MagicMock())
    monkeypatch.setattr("src.stage3.stage3_worker.record_error", MagicMock())
    reset_for_tests()
    return worker


def test_shipped_cost_controls_are_null_and_kafka_block_unchanged():
    from pathlib import Path

    path = Path(__file__).resolve().parents[ROOT_CONFIG_PARENTS] / "config.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["cost_controls"] == {
        "stage3_max_gpu_minutes_per_day": None,
        "stage4_max_gpu_minutes_per_day": None,
        "stage4_max_ocr_pages_per_day": None,
    }
    assert data["carbon_scheduling"]["enabled"] is False
    topics = data["kafka"]["topics"]
    assert topics["scraped_items"] == "scraped-items"
    assert topics["dead_letter"] == "scraped-items-dlq"
    assert topics["validation_failures"] == "validation_failures"


def test_missing_or_null_caps_do_not_block(monkeypatch: pytest.MonkeyPatch):
    clock = FakeClock(datetime(2026, 1, 2, 3, tzinfo=UTC))
    monkeypatch.setattr(cost_controls, "configured_cap", lambda _key: None)
    budget = CostBudget(clock=clock.now, ledger={})
    assert budget.cap("stage3", "gpu_minutes") is None
    assert budget.block_reason("stage3") is None
    assert budget.block_reason("stage4") is None

    explicit = _budget(
        clock,
        **{
            STAGE3_GPU_MINUTES_KEY: None,
            STAGE4_GPU_MINUTES_KEY: None,
            STAGE4_OCR_PAGES_KEY: None,
        },
    )
    assert explicit.block_reason("stage3") is None
    assert explicit.block_reason("stage4") is None


def test_bool_cap_is_uncapped_and_negative_cap_is_exhausted():
    clock = FakeClock(datetime(2026, 5, 1, tzinfo=UTC))
    assert _budget(clock, **{STAGE3_GPU_MINUTES_KEY: True}).block_reason("stage3") is None
    negative = _budget(clock, **{STAGE3_GPU_MINUTES_KEY: -4})
    assert negative.cap("stage3", "gpu_minutes") == 0
    assert negative.block_reason("stage3") == STATUS_BUDGET_EXHAUSTED


def test_fake_clock_day_rolls_the_ledger_back_to_the_cap():
    clock = FakeClock(datetime(2026, 7, 1, 23, tzinfo=UTC))
    budget = _budget(clock, **{STAGE4_OCR_PAGES_KEY: 1})
    assert budget.charge("stage4", OCR_PAGES, 1) == 0
    assert budget.block_reason("stage4") == STATUS_BUDGET_EXHAUSTED
    clock.advance(seconds=3600)  # 2026-07-02 00:00 UTC
    assert budget.remaining("stage4", OCR_PAGES) == 1
    assert budget.block_reason("stage4") is None
    assert _gauge("stage4", OCR_PAGES) == 1


def test_default_ledger_is_shared_by_later_workers():
    cost_controls.reset_ledger_for_tests()
    try:
        clock = FakeClock(datetime(2026, 8, 1, tzinfo=UTC))
        caps = {STAGE4_OCR_PAGES_KEY: 1}
        first = CostBudget(clock=clock.now, caps=caps)
        assert first.charge("stage4", OCR_PAGES, 1) == 0
        second = CostBudget(clock=clock.now, caps=caps)
        assert second.block_reason("stage4") == STATUS_BUDGET_EXHAUSTED
        assert second.remaining("stage4", OCR_PAGES) == 0
    finally:
        cost_controls.reset_ledger_for_tests()


def test_ocr_page_count_prefers_explicit_pages_then_pdf():
    assert ocr_pages_for({"url": "https://a/x.html", "page_count": 3}) == 3
    assert ocr_pages_for({"url": "https://a/x.PDF?dl=1"}) == 1
    assert ocr_pages_for({"url": "https://a/x.html", "is_pdf": False}) == 0
    assert ocr_pages_for({"page_count": True, "url": "https://a/x.html"}) == 0


def _arm_stage3(worker: Stage3Worker, clock: FakeClock, cap: float | None) -> list[str]:
    calls: list[str] = []
    real = Stage3Worker._summarize_document

    async def wrapped(self: Stage3Worker, doc: dict[str, Any]) -> dict[str, Any] | None:
        calls.append(doc["url"])
        clock.advance(seconds=60)
        return await real(self, doc)

    worker._summarize_document = wrapped.__get__(worker, Stage3Worker)  # type: ignore[method-assign]
    worker._cost_budget_obj = _budget(clock, **{STAGE3_GPU_MINUTES_KEY: cap})  # type: ignore[attr-defined]
    text_a = "alpha " * 40
    text_b = "beta " * 40
    worker.delta.tables["stage2_page_analysis"] = [  # type: ignore[attr-defined]
        _stage3_doc("https://a/", text_a),
        _stage3_doc("https://b/", text_b),
    ]
    worker.delta.tables["stage3_summaries"] = []  # type: ignore[attr-defined]
    return calls


def test_null_stage3_cap_summarizes_every_document(stage3: Stage3Worker, monkeypatch: pytest.MonkeyPatch):
    """Shipped cost_controls are null: the uncapped path summarizes every document."""
    calls: list[str] = []
    real = Stage3Worker._summarize_document

    async def wrapped(self: Stage3Worker, doc: dict[str, Any]) -> dict[str, Any] | None:
        calls.append(doc["url"])
        return await real(self, doc)

    monkeypatch.setattr(Stage3Worker, "_summarize_document", wrapped)
    stage3.delta.tables["stage2_page_analysis"] = [  # type: ignore[attr-defined]
        _stage3_doc("https://a/", "alpha " * 40),
        _stage3_doc("https://b/", "beta " * 40),
    ]
    stage3.delta.tables["stage3_summaries"] = []  # type: ignore[attr-defined]
    assert asyncio.run(stage3.run()) == 2
    assert calls == ["https://a/", "https://b/"]
    assert stage3.budget_status is None


def test_stage3_tiny_gpu_cap_stops_before_the_next_summarize(stage3: Stage3Worker, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.WARNING)
    clock = FakeClock(datetime(2026, 2, 2, tzinfo=UTC))
    calls = _arm_stage3(stage3, clock, 0.01)
    assert asyncio.run(stage3.run()) == 1
    assert calls == ["https://a/"]
    assert stage3.budget_status == STATUS_BUDGET_EXHAUSTED
    assert _gauge("stage3", "gpu_minutes") == 0
    assert "budget_exhausted" in caplog.text
    assert "stopping batch" in caplog.text
    assert "not calling GPU, OCR, network, or paid API" in caplog.text

    clock.advance(days=1)
    assert asyncio.run(stage3.run()) == 1
    assert calls == ["https://a/", "https://b/"]


def test_stage3_zero_cap_does_not_summarize(stage3: Stage3Worker):
    clock = FakeClock(datetime(2026, 2, 3, tzinfo=UTC))
    calls = _arm_stage3(stage3, clock, 0)
    assert asyncio.run(stage3.run()) == 0
    assert calls == []
    assert stage3.budget_status == STATUS_BUDGET_EXHAUSTED
    assert _gauge("stage3", "gpu_minutes") == 0


def test_stage4_null_caps_still_fetch():
    """No injected budget: shipped null caps must not skip fetch or summarize."""
    rec = Recorder()
    proc = FakeProcessor(
        rec,
        texts={"https://a/": "x" * 20, "https://b/": "y" * 20},
        summaries={"https://a/": "sa", "https://b/": "sb"},
    )
    delta = FakeDelta(rec, [_row("https://a/"), _row("https://b/")])
    worker = _worker(rec, proc, delta)
    assert asyncio.run(worker._run_traced()) == 2
    assert [c for c in rec.calls if c[0] == "fetch"] == [("fetch", "https://a/"), ("fetch", "https://b/")]
    assert worker.budget_status is None


def test_stage4_zero_gpu_cap_does_not_fetch(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.WARNING)
    clock = FakeClock(datetime(2026, 3, 2, tzinfo=UTC))
    rec = Recorder()
    proc = FakeProcessor(rec, texts={"https://a/": "x" * 20}, summaries={"https://a/": "s"})
    delta = FakeDelta(rec, [_row("https://a/")])
    worker = _worker(rec, proc, delta)
    worker._cost_budget_obj = _budget(clock, **{STAGE4_GPU_MINUTES_KEY: 0})  # type: ignore[attr-defined]
    assert asyncio.run(worker._run_traced()) == 0
    assert rec.calls == []
    assert worker.budget_status == STATUS_BUDGET_EXHAUSTED
    assert _gauge("stage4", "gpu_minutes") == 0
    assert delta.tables["stage4_large_docs"][0]["status"] == "pending"
    assert "not calling GPU, OCR, network, or paid API" in caplog.text


def test_stage4_tiny_ocr_cap_stops_before_network_and_resets_next_day():
    clock = FakeClock(datetime(2026, 3, 3, 12, tzinfo=UTC))
    rec = Recorder()
    urls = ("https://a/one.pdf", "https://b/two.pdf")
    proc = FakeProcessor(
        rec,
        texts={urls[0]: "p" * 30, urls[1]: "q" * 30},
        summaries={urls[0]: "s1", urls[1]: "s2"},
    )
    delta = FakeDelta(rec, [_row(urls[0], is_pdf=True), _row(urls[1], is_pdf=True)])
    worker = _worker(rec, proc, delta)
    worker._cost_budget_obj = _budget(clock, **{STAGE4_OCR_PAGES_KEY: 1})  # type: ignore[attr-defined]

    assert asyncio.run(worker._run_traced()) == 1
    assert ("fetch", urls[0]) in rec.calls
    assert ("fetch", urls[1]) not in rec.calls
    assert ("summarize", urls[1]) not in rec.calls
    assert worker.budget_status == STATUS_BUDGET_EXHAUSTED
    assert _gauge("stage4", OCR_PAGES) == 0
    statuses = {row["url"]: row["status"] for row in delta.tables["stage4_large_docs"]}
    assert statuses[urls[1]] == "pending"

    clock.advance(days=1)
    rec.calls.clear()
    assert asyncio.run(worker._run_traced()) == 1
    assert ("fetch", urls[1]) in rec.calls
    assert ("summarize", urls[1]) in rec.calls


def test_stage4_tiny_gpu_cap_uses_the_fake_clock_and_skips_the_next_fetch():
    clock = FakeClock(datetime(2026, 3, 4, tzinfo=UTC))
    rec = Recorder()

    class _Advance(FakeProcessor):
        def _fetch_content(self, url: str, is_pdf: bool = False) -> tuple[str, str]:
            clock.advance(seconds=120)
            return super()._fetch_content(url, is_pdf)

    proc = _Advance(
        rec,
        texts={"https://a/": "x" * 20, "https://b/": "y" * 20},
        summaries={"https://a/": "sa", "https://b/": "sb"},
    )
    delta = FakeDelta(rec, [_row("https://a/"), _row("https://b/")])
    worker = _worker(rec, proc, delta)
    # 0.01 minutes is the whole day's GPU budget. The fake clock jumps two minutes
    # inside the first fetch, so the second document is not fetched.
    worker._cost_budget_obj = _budget(clock, **{STAGE4_GPU_MINUTES_KEY: 0.01})  # type: ignore[attr-defined]
    assert asyncio.run(worker._run_traced()) == 1
    assert ("fetch", "https://a/") in rec.calls
    assert ("fetch", "https://b/") not in rec.calls
    assert _gauge("stage4", "gpu_minutes") == 0
    assert worker.budget_status == STATUS_BUDGET_EXHAUSTED


def test_tiny_ocr_cap_refuses_a_document_that_does_not_fit():
    clock = FakeClock(datetime(2026, 3, 5, tzinfo=UTC))
    rec = Recorder()
    url = "https://a/big.pdf"
    proc = FakeProcessor(rec, texts={url: "p" * 10}, summaries={url: "s"})
    delta = FakeDelta(rec, [_row(url, is_pdf=True, ocr_pages=4)])
    worker = _worker(rec, proc, delta)
    worker._cost_budget_obj = _budget(clock, **{STAGE4_OCR_PAGES_KEY: 0.5})  # type: ignore[attr-defined]
    assert asyncio.run(worker._run_traced()) == 0
    assert rec.calls == []
    assert worker.budget_status == STATUS_BUDGET_EXHAUSTED
    assert _gauge("stage4", OCR_PAGES) == pytest.approx(0.5)
