"""#645: js_spider_queue is fed only with real JS shells, and status updates don't drop rows."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from scrapy.http import HtmlResponse, Request

from src.lakehouse.lakehouse_manager import LakehouseManager
from src.stage1.experimental.js_spider import JavaScriptSpider
from src.stage1.scout_spider import ScoutSpider
from src.utils.delta import DeltaHelper

SPA = (b"<html><head><title>App</title><script src='/static/js/main.chunk.js'></script>"
       b"<script src='/static/js/react.production.min.js'></script></head>"
       b"<body><div id='root'></div><noscript>You need to enable JavaScript to run this app.</noscript>"
       b"</body></html>")
STATIC = (b"<html><head><title>Admissions</title></head><body><h1>Admissions</h1>"
          b"<p>" + b"Apply to UConn today. " * 40 + b"</p>"
          b"<a href='https://www.uconn.edu/apply'>Apply</a><a href='https://www.uconn.edu/visit'>Visit</a>"
          b"</body></html>")


def _config(values):
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: values.get(key, default)
    return cfg


def _spider(values=None):
    with patch("src.stage1.scout_spider.get_delta_manager"), \
            patch.object(ScoutSpider, "_discover_and_add_sitemap_urls"), \
            patch("src.core.config.get_config", return_value=_config(values or {})):
        s = ScoutSpider()
    s.expand_seeds = False
    return s


def _resp(body, url="https://www.uconn.edu/app"):
    return HtmlResponse(url=url, body=body, headers={"Content-Type": "text/html; charset=utf-8"},
                        encoding="utf-8", request=Request(url, meta={"depth": 0}))


def _js_items(items):
    return [i for i in items if isinstance(i, dict) and i.get("target_spider") == "javascript"]


def _stage2_items(items):
    return [i for i in items if isinstance(i, dict) and i.get("target_stage") == "stage2"]


def test_config_reads_stage1_section_not_phantom_stages_path():
    s = _spider({"stage1.expand_seeds": False, "stage1.parse_sitemaps": False,
                 "stage1.js_rendering_enabled": True, "stage1.js_confidence_threshold": 0.8})
    assert s.parse_sitemaps is False
    assert s.js_rendering_enabled is True
    assert s.js_confidence_threshold == 0.8


def test_js_rendering_off_by_default_and_never_queues_js():
    s = _spider()
    assert s.js_rendering_enabled is False
    items = list(s.parse(_resp(SPA)))
    assert _js_items(items) == []
    assert s.scout_stats["html_queued_js"] == 0


def test_static_links_go_to_stage2_only_not_dual_queued():
    s = _spider({"stage1.js_rendering_enabled": True})
    items = list(s.parse(_resp(STATIC, url="https://www.uconn.edu/admissions")))
    assert _js_items(items) == []  # a plain page is not a JS shell; its links are not JS-queued
    assert s.scout_stats["html_queued_js"] == 0


def test_js_shell_queued_once_when_enabled():
    s = _spider({"stage1.js_rendering_enabled": True})
    items = list(s.parse(_resp(SPA)))
    js = _js_items(items)
    assert [i["url"] for i in js] == ["https://www.uconn.edu/app"]
    assert js[0]["status"] == "pending"
    assert s.scout_stats["html_queued_js"] == 1


def test_threshold_gates_detection():
    s = _spider({"stage1.js_rendering_enabled": True, "stage1.js_confidence_threshold": 1.01})
    assert _js_items(list(s.parse(_resp(SPA)))) == []


def test_detector_error_is_not_js():
    s = _spider({"stage1.js_rendering_enabled": True})
    with patch("src.stage1.js_detection.JSDetector", side_effect=RuntimeError("boom")):
        assert s._requires_js(_resp(SPA)) is False


# --- JavaScriptSpider status update -------------------------------------------------

def _row(url, status="pending"):
    return {"url": url, "parent_url": "https://www.uconn.edu/", "priority": 1, "status": status,
            "queued_at": "2026-10-08T00:00:00", "queued_by": "scout", "target_spider": "javascript"}


@pytest.fixture
def js_spider(tmp_path):
    helper = DeltaHelper(tmp_path / "lake")
    helper._manager = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    helper.write("js_spider_queue", [_row("https://a.uconn.edu/"), _row("https://b.uconn.edu/"),
                                     _row("https://c.uconn.edu/")], mode="append", async_write=False)
    s = JavaScriptSpider.__new__(JavaScriptSpider)
    s.delta = helper
    s.completed_urls, s.failed_urls = [], []
    return s


def _statuses(helper):
    return {r["url"]: r["status"] for r in helper.read("js_spider_queue")}


def test_status_merge_keeps_rows_appended_during_crawl(js_spider):
    js_spider.completed_urls = ["https://a.uconn.edu/", "https://a.uconn.edu/"]
    js_spider.failed_urls = ["https://b.uconn.edu/"]
    # Scout appends while the JS spider is running (old code then overwrote the table).
    js_spider.delta.write("js_spider_queue", [_row("https://d.uconn.edu/")], mode="append", async_write=False)
    assert js_spider._update_queue_status() == 2
    assert _statuses(js_spider.delta) == {
        "https://a.uconn.edu/": "completed",
        "https://b.uconn.edu/": "failed",
        "https://c.uconn.edu/": "pending",
        "https://d.uconn.edu/": "pending",
    }


def test_status_merge_on_table_already_carrying_completed_at(js_spider):
    # Tables touched by the old overwrite path already have a string completed_at.
    js_spider.completed_urls = ["https://a.uconn.edu/"]
    assert js_spider._update_queue_status() == 1
    js_spider.completed_urls, js_spider.failed_urls = ["https://c.uconn.edu/"], []
    js_spider.delta.write("js_spider_queue", [_row("https://e.uconn.edu/")], mode="append", async_write=False)
    assert js_spider._update_queue_status() == 1
    rows = {r["url"]: r for r in js_spider.delta.read("js_spider_queue")}
    assert rows["https://c.uconn.edu/"]["status"] == "completed" and rows["https://c.uconn.edu/"]["completed_at"]
    assert rows["https://a.uconn.edu/"]["status"] == "completed"
    assert rows["https://e.uconn.edu/"]["status"] == "pending"
    assert len(rows) == 4


def test_status_merge_does_not_insert_unqueued_urls(js_spider):
    js_spider.completed_urls = ["https://priority-only.uconn.edu/"]
    assert js_spider._update_queue_status() == 0
    assert len(js_spider.delta.read("js_spider_queue")) == 3


def test_failure_handler_records_url():
    s = JavaScriptSpider.__new__(JavaScriptSpider)
    s.failed_urls = []
    ledger = MagicMock()

    async def _release(_f):
        return None
    ledger.release_from_failure = _release
    s._ledger = lambda: ledger
    failure = SimpleNamespace(getErrorMessage=lambda: "timeout", request=Request("https://x.uconn.edu/"))
    import asyncio
    asyncio.run(s.handle_error(failure))
    assert s.failed_urls == ["https://x.uconn.edu/"]


def test_merge_into_can_update_a_column_the_table_lacks(tmp_path):
    # deltalake 1.2 raised "Duplicate field name" for this (merge_schema + matched update).
    m = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    assert m.merge_into("js_spider_queue", [_row("https://a.uconn.edu/"), _row("https://b.uconn.edu/")],
                        merge_key="url", update_columns=["status"]) == 2
    n = m.merge_into("js_spider_queue",
                     [{"url": "https://a.uconn.edu/", "status": "completed", "completed_at": "t1"},
                      {"url": "https://new.uconn.edu/", "status": "pending", "completed_at": None}],
                     merge_key="url", update_columns=["status", "completed_at"])
    assert n == 2
    rows = {r["url"]: r for r in m.read("js_spider_queue")}
    assert rows["https://a.uconn.edu/"]["completed_at"] == "t1"
    assert rows["https://b.uconn.edu/"]["completed_at"] is None
    assert set(rows) == {"https://a.uconn.edu/", "https://b.uconn.edu/", "https://new.uconn.edu/"}


def test_orchestrator_reports_pending_js_pages(caplog):
    from src.orchestrator.pipeline_orchestrator import PipelineOrchestrator, PipelineStats
    orch = PipelineOrchestrator.__new__(PipelineOrchestrator)
    orch.stats = PipelineStats()
    orch.delta = MagicMock()
    orch.delta.read.return_value = [{"status": "pending"}, {"status": "completed"}, {"status": "pending"}]
    assert orch._report_js_queue() == 2
    assert orch.stats.stage1_js_pending == 2
    assert any("scrapy crawl javascript" in r.getMessage() for r in caplog.records)
    orch.delta.read.side_effect = RuntimeError("no table")
    assert orch._report_js_queue() == 0
