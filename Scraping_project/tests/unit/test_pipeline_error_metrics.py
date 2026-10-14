"""Handled pipeline errors are logged and counted, never swallowed (#219)."""

from __future__ import annotations

import ast
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

from prometheus_client import REGISTRY
from scrapy.settings import Settings
from scrapy.statscollectors import MemoryStatsCollector

from src.pipelines import GrafanaSummaryPipeline, OffsiteCandidatePipeline

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _errors(stage: str, reason: str) -> float:
    return REGISTRY.get_sample_value("pipeline_errors_total", {"stage": stage, "reason": reason}) or 0.0


def test_no_bare_pass_in_pipeline_exception_handlers():
    tree = ast.parse((PROJECT_ROOT / "src" / "pipelines.py").read_text(encoding="utf-8"))
    bare = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
    ]
    assert bare == []


def test_offsite_metric_import_error_increments_counter(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="src.pipelines")
    monkeypatch.setitem(sys.modules, "src.scrapy_prometheus", None)
    before = _errors("offsite", "metrics_import")
    OffsiteCandidatePipeline._count_saved(SimpleNamespace(name="offsite_err"), 2)
    assert _errors("offsite", "metrics_import") == before + 1
    assert any("stage=offsite" in rec.message and "reason=metrics_import" in rec.message for rec in caplog.records)


def test_successful_offsite_count_is_not_an_error():
    before = _errors("offsite", "metrics_import")
    OffsiteCandidatePipeline._count_saved(SimpleNamespace(name="offsite_ok_219"), 1)
    assert _errors("offsite", "metrics_import") == before


class _BrokenMetric:
    def labels(self, **kwargs):
        raise RuntimeError("exporter down")


def test_summary_metric_failure_keeps_the_item_and_counts_the_error(monkeypatch, caplog):
    import src.scrapy_prometheus as sp

    caplog.set_level(logging.WARNING, logger="src.pipelines")
    crawler = SimpleNamespace(settings=Settings())
    crawler.stats = MemoryStatsCollector(crawler)
    spider = SimpleNamespace(name="summary_metric_err", crawler=crawler)
    monkeypatch.setattr(sp, "CRAWLER_CONTENT_SUMMARY", _BrokenMetric())
    monkeypatch.setattr(sp, "CRAWLER_SUMMARY_SKIPPED", _BrokenMetric())

    pipeline = GrafanaSummaryPipeline()
    pipeline.SAMPLE_RATE = 1
    pipeline.BATCH_SIZE = 1
    before = _errors("summary", "metrics_emit")
    item = {"url": "https://uconn.edu/a", "title": "A"}
    assert pipeline.process_item(item, spider) is item
    assert _errors("summary", "metrics_emit") == before + 1
    assert spider.crawler.stats.get_value("summary_skipped/export_error") == 1
    assert any("stage=summary" in rec.message and "reason=metrics_emit" in rec.message for rec in caplog.records)
