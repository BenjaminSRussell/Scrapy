"""GrafanaSummaryPipeline is optional telemetry: it must never fail the crawl (#462)."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from prometheus_client import REGISTRY
from scrapy.statscollectors import MemoryStatsCollector

import src.scrapy_prometheus as sp
from src.pipelines import GrafanaSummaryPipeline

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _spider(name="summary_test"):
    from scrapy.settings import Settings

    crawler = SimpleNamespace(settings=Settings())
    crawler.stats = MemoryStatsCollector(crawler)
    spider = SimpleNamespace(name=name, crawler=crawler)
    return spider


def _pipeline(sample_rate=1, batch=2):
    p = GrafanaSummaryPipeline()
    p.SAMPLE_RATE = sample_rate
    p.BATCH_SIZE = batch
    return p


def _skipped(spider, reason):
    return REGISTRY.get_sample_value(
        "scrapy_crawler_summary_skipped_total", {"spider": spider.name, "reason": reason}
    ) or 0.0


def test_summary_still_exports_when_metrics_available():
    spider = _spider("summary_ok")
    p = _pipeline(batch=2)
    for i in range(2):
        assert p.process_item({"url": f"https://uconn.edu/{i}", "title": f"Page {i}"}, spider)["title"] == f"Page {i}"
    assert REGISTRY.get_sample_value("scrapy_crawler_content_summary", {"spider": "summary_ok"}) == 2
    assert p.sampled_content == []
    assert spider.crawler.stats.get_value("summary_skipped") is None


class _BrokenGauge:
    def labels(self, **kw):
        raise RuntimeError("exporter exploded")


def test_export_failure_keeps_the_item_and_counts_a_skip(monkeypatch):
    spider = _spider("summary_export_err")
    monkeypatch.setattr(sp, "CRAWLER_CONTENT_SUMMARY", _BrokenGauge())
    p = _pipeline(batch=1)
    before = _skipped(spider, "export_error")
    item = {"url": "https://uconn.edu/a", "title": "A"}
    assert p.process_item(item, spider) is item
    assert spider.crawler.stats.get_value("summary_skipped") == 1
    assert spider.crawler.stats.get_value("summary_skipped/export_error") == 1
    assert _skipped(spider, "export_error") == before + 1
    assert p.sampled_content == []  # bounded: failed batches are dropped, not retried forever


def test_missing_metrics_module_is_a_skip_not_a_crash(monkeypatch):
    spider = _spider("summary_nodeps")
    monkeypatch.setitem(sys.modules, "src.scrapy_prometheus", None)  # import raises ImportError
    p = _pipeline(batch=1)
    item = {"url": "https://uconn.edu/b", "text": "body"}
    assert p.process_item(item, spider) is item
    assert spider.crawler.stats.get_value("summary_skipped/deps_unavailable") == 1


def test_metrics_disabled_is_a_quiet_skip(monkeypatch):
    spider = _spider("summary_disabled")
    monkeypatch.setattr(sp, "CRAWLER_CONTENT_SUMMARY", None)
    p = _pipeline(batch=1)
    p.process_item({"url": "https://uconn.edu/c", "title": "C"}, spider)
    assert spider.crawler.stats.get_value("summary_skipped/metrics_disabled") == 1


def test_sampling_failure_keeps_the_item(monkeypatch):
    spider = _spider("summary_sample_err")
    p = _pipeline()

    def boom(adapter):
        raise ValueError("weird item")

    monkeypatch.setattr(p, "_extract_text_content", boom)
    item = {"url": "https://uconn.edu/d"}
    assert p.process_item(item, spider) is item
    assert spider.crawler.stats.get_value("summary_skipped/sample_error") == 1


def test_spider_close_never_raises(monkeypatch):
    spider = _spider("summary_close")
    p = _pipeline(batch=100)
    p.process_item({"url": "https://uconn.edu/e", "title": "E"}, spider)

    def boom(_spider):
        raise RuntimeError("late failure")

    monkeypatch.setattr(p, "_generate_and_export_summary", boom)
    p.spider_closed(spider)  # must not raise
    assert spider.crawler.stats.get_value("summary_skipped/close_error") == 1


def test_spider_close_flushes_remaining_samples():
    spider = _spider("summary_flush")
    p = _pipeline(batch=100)
    for i in range(3):
        p.process_item({"url": f"https://uconn.edu/f{i}", "title": "F"}, spider)
    p.spider_closed(spider)
    assert REGISTRY.get_sample_value("scrapy_crawler_content_summary", {"spider": "summary_flush"}) == 3


CRAWL_SCRIPT = textwrap.dedent(
    """
    import json, sys
    # Simulate an install without the summarization extras (#462).
    for mod in ("torch", "transformers", "sentence_transformers"):
        sys.modules[mod] = None
    import scrapy
    from scrapy.crawler import CrawlerProcess
    from src.pipelines import GrafanaSummaryPipeline

    GrafanaSummaryPipeline.SAMPLE_RATE = 1
    GrafanaSummaryPipeline.BATCH_SIZE = 2

    class S(scrapy.Spider):
        name = "summary_no_ml"
        start_urls = ["data:,hello"]

        def parse(self, response):
            for i in range(5):
                yield {"url": f"https://uconn.edu/{i}", "title": f"t{i}"}

    stats = {}
    proc = CrawlerProcess({
        "ITEM_PIPELINES": {"src.pipelines.GrafanaSummaryPipeline": 900},
        "LOG_LEVEL": "ERROR",
        "TELNETCONSOLE_ENABLED": False,
        "REQUEST_FINGERPRINTER_IMPLEMENTATION": "2.7",
    })
    crawler = proc.create_crawler(S)
    proc.crawl(crawler)
    proc.start()
    s = crawler.stats.get_stats()
    print(json.dumps({
        "finish_reason": s.get("finish_reason"),
        "items": s.get("item_scraped_count"),
        "skipped": s.get("summary_skipped", 0),
        "ml_loaded": [m for m in ("torch", "transformers", "sentence_transformers") if sys.modules.get(m) is not None],
    }))
    """
)


def test_real_crawl_closes_cleanly_without_ml_extras():
    out = subprocess.run(
        [sys.executable, "-c", CRAWL_SCRIPT],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert result == {"finish_reason": "finished", "items": 5, "skipped": 0, "ml_loaded": []}
