"""#645: Scout -> js_spider_queue -> javascript spider -> Stage 2 hop contract."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from scrapy.http import HtmlResponse, Request

from src.orchestrator import pipeline_orchestrator as po
from src.stage1.js_queue import count_pending, js_spider_enabled


class _Cfg:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class _Lake:
    def __init__(self, tables=None):
        self.tables = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}

    def read(self, name, *a, **k):
        return [dict(r) for r in self.tables.get(name, [])]

    def write(self, name, data, mode="append", **k):
        rows = [dict(r) for r in data]
        self.tables[name] = rows if mode == "overwrite" else self.tables.get(name, []) + rows
        return True


QUEUE = [
    {"url": "https://a.example/js", "status": "pending"},
    {"url": "https://b.example/js"},  # no status: pending (Scout contract)
    {"url": "https://c.example/done", "status": "completed"},
    {"url": "https://d.example/fail", "status": "failed"},
]


# --- flag resolution -------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("ENABLE_JS_SPIDER", raising=False)
    monkeypatch.delenv("JS_DRAIN_TIMEOUT_SECONDS", raising=False)


def test_flag_defaults_on():
    assert js_spider_enabled(_Cfg({})) is True


@pytest.mark.parametrize("key", ["stage1.enable_js_spider", "stages.stage1.enable_js_spider"])
@pytest.mark.parametrize("value,expected", [(False, False), ("false", False), ("0", False), (True, True), ("yes", True)])
def test_flag_from_config(key, value, expected):
    assert js_spider_enabled(_Cfg({key: value})) is expected


def test_env_overrides_config_and_arg_overrides_env(monkeypatch):
    monkeypatch.setenv("ENABLE_JS_SPIDER", "off")
    assert js_spider_enabled(_Cfg({"stage1.enable_js_spider": True})) is False
    assert js_spider_enabled(_Cfg({}), enabled=True) is True
    monkeypatch.setenv("ENABLE_JS_SPIDER", "  ")  # blank: ignored
    assert js_spider_enabled(_Cfg({"stage1.enable_js_spider": False})) is False


def test_config_yml_ships_flag_on():
    import yaml

    cfg = yaml.safe_load((Path(__file__).resolve().parents[3] / "config.yml").read_text())
    assert cfg["stage1"]["enable_js_spider"] is True


def test_count_pending():
    assert count_pending(QUEUE) == 2
    assert count_pending([]) == 0
    assert count_pending(None) == 0


# --- orchestrator drain ----------------------------------------------------


@pytest.fixture
def orch(monkeypatch):
    lake = _Lake({"js_spider_queue": QUEUE})
    monkeypatch.setattr(po, "get_delta", lambda: lake)
    o = po.PipelineOrchestrator()
    o.lake = lake
    return o


def test_drain_disabled_never_spawns(orch, monkeypatch):
    monkeypatch.setenv("ENABLE_JS_SPIDER", "0")
    with patch.object(po.subprocess, "run") as run:
        assert orch.run_js_queue() == 0
    run.assert_not_called()
    assert orch.stats.stage1_js_pending == 2  # still reported


def test_drain_empty_queue_never_spawns(orch):
    orch.lake.tables["js_spider_queue"] = [{"url": "x", "status": "completed"}]
    with patch.object(po.subprocess, "run") as run:
        assert orch.run_js_queue(enabled=True) == 0
    run.assert_not_called()


def test_drain_runs_javascript_spider_in_child_process(orch):
    """A second in-process CrawlerProcess would hit ReactorNotRestartable."""

    def fake_run(cmd, cwd, timeout, check):
        assert cmd == [sys.executable, "-m", "scrapy", "crawl", "javascript"]
        assert (Path(cwd) / "scrapy.cfg").is_file()
        assert timeout == 3600
        for row in orch.lake.tables["js_spider_queue"]:
            if row.get("status") in (None, "pending"):
                row["status"] = "completed"
        return SimpleNamespace(returncode=0)

    seen = []
    with patch.object(po.subprocess, "run", side_effect=fake_run), patch(
        "src.scrapy_prometheus.set_pipeline_js_queue_pending", side_effect=seen.append
    ):
        assert orch.run_js_queue(enabled=True) == 0
    assert seen == [2, 0]
    assert orch.stats.stage1_js_pending == 0
    assert orch.stats.stage1_js_drain_error is None


def test_drain_timeout_and_exit_code_are_recorded_not_raised(orch, monkeypatch):
    monkeypatch.setenv("JS_DRAIN_TIMEOUT_SECONDS", "5")
    with patch.object(po.subprocess, "run", side_effect=subprocess.TimeoutExpired("scrapy", 5)):
        assert orch.run_js_queue(enabled=True) == 2
    assert orch.stats.stage1_js_drain_error == "javascript spider timed out"
    with patch.object(po.subprocess, "run", return_value=SimpleNamespace(returncode=3)):
        orch.run_js_queue(enabled=True)
    assert orch.stats.stage1_js_drain_error == "javascript spider exited 3"


class _OK2:
    def __init__(self, **kwargs):
        pass

    async def run(self):
        return {"analyzed": 1, "quality_docs": 1, "massive_docs": 0, "errors": 0}


class _OK34(_OK2):
    async def run(self):
        return 1


def _wire(o, monkeypatch, order, js):
    monkeypatch.setattr(o, "run_stage1", lambda url_limit=None, spider_name="scout": order.append("stage1") or 0)
    monkeypatch.setattr(o, "run_js_queue", js)

    class _Rec2(_OK2):
        async def run(self):
            order.append("stage2")
            return await super().run()

    monkeypatch.setattr(po, "Stage2Worker", _Rec2)
    monkeypatch.setattr(po, "Stage3Worker", _OK34)
    monkeypatch.setattr(po, "Stage4Worker", _OK34)


def test_full_pipeline_drains_between_scout_and_stage2(orch, monkeypatch):
    order = []
    _wire(orch, monkeypatch, order, lambda: order.append("js") or 0)
    stats = asyncio.run(orch.run_full_pipeline())
    assert order == ["stage1", "js", "stage2"]
    assert stats.status == "complete"


def test_full_pipeline_survives_drain_failure(orch, monkeypatch):
    order = []

    def boom():
        raise RuntimeError("playwright missing")

    _wire(orch, monkeypatch, order, boom)
    stats = asyncio.run(orch.run_full_pipeline())
    assert order == ["stage1", "stage2"]
    assert stats.status == "complete"
    assert "playwright missing" in stats.stage1_js_drain_error


def test_metric_setter_is_safe():
    from src import scrapy_prometheus as sp

    sp.set_pipeline_js_queue_pending(4)
    if sp.PIPELINE_JS_QUEUE_PENDING is not None:
        assert sp.PIPELINE_JS_QUEUE_PENDING._value.get() == 4


# --- Scout guardrail -------------------------------------------------------


def _scout_items(monkeypatch, js_on):
    from src.stage1.scout_spider import ScoutSpider

    monkeypatch.setenv("ENABLE_JS_SPIDER", "1" if js_on else "0")
    with patch("src.stage1.scout_spider.get_delta_manager"), patch("src.stage1.scout_spider.get_delta"):
        spider = ScoutSpider(allowed_domains=["example.com"])
    spider.expand_seeds = False
    # Dedup claims hashes in a shared Redis set; keep this test hermetic.
    spider._deduplicate_urls = lambda urls: (list(urls), {u: spider._hash_url(u) for u in urls})
    body = b"<html><body><h1>Hub</h1>" + b"<p>text </p>" * 50 + b'<a href="/page1">one</a></body></html>'
    resp = HtmlResponse(
        url="https://example.com/",
        body=body,
        encoding="utf-8",
        headers={"Content-Type": "text/html; charset=utf-8"},
        request=Request("https://example.com/", meta={"depth": 0}),
    )
    items = [x for x in spider.parse(resp) if isinstance(x, dict)]
    return spider, [i for i in items if i.get("url") == "https://example.com/page1"]


def test_scout_js_on_queues_js_and_stage2(monkeypatch):
    spider, items = _scout_items(monkeypatch, js_on=True)
    assert {i.get("target_spider") or i.get("target_stage") for i in items} == {"javascript", "stage2"}
    assert spider.scout_stats["html_queued_js"] == 1


def test_scout_js_off_never_fills_js_queue(monkeypatch):
    spider, items = _scout_items(monkeypatch, js_on=False)
    assert [i.get("target_stage") for i in items] == ["stage2"]
    assert not any(i.get("target_spider") == "javascript" for i in items)
    assert spider.scout_stats["html_queued_js"] == 0


# --- javascript spider -----------------------------------------------------


def _js_spider(lake):
    from src.stage1.experimental.js_spider import JavaScriptSpider

    spider = JavaScriptSpider.__new__(JavaScriptSpider)
    spider.delta = lake
    spider.rendered_count = 0
    spider.completed_urls = []
    spider.failed_urls = {}
    spider._ledger = lambda: SimpleNamespace(open_count=0)
    return spider


def test_js_spider_marks_failed_renders_so_pending_drains():
    lake = _Lake({"js_spider_queue": QUEUE})
    spider = _js_spider(lake)
    spider.completed_urls = ["https://a.example/js"]
    spider.failed_urls = {"https://b.example/js": "timeout"}
    spider.closed("finished")
    rows = {r["url"]: r for r in lake.tables["js_spider_queue"]}
    assert rows["https://a.example/js"]["status"] == "completed"
    assert rows["https://b.example/js"]["status"] == "failed"
    assert rows["https://b.example/js"]["error"] == "timeout"
    assert rows["https://c.example/done"]["status"] == "completed"
    assert count_pending(lake.tables["js_spider_queue"]) == 0


def test_js_spider_records_failure_from_errback():
    spider = _js_spider(_Lake())

    async def release(_failure):
        return None

    spider._ledger = lambda: SimpleNamespace(release_from_failure=release, open_count=0)
    failure = MagicMock()
    failure.request.url = "https://a.example/js"
    failure.getErrorMessage.return_value = "net::ERR_TIMED_OUT"
    asyncio.run(spider.handle_error(failure))
    assert spider.failed_urls == {"https://a.example/js": "net::ERR_TIMED_OUT"}


def test_js_spider_hands_discoveries_to_stage2():
    spider = _js_spider(_Lake())
    spider.name = "javascript"
    spider.seed_manager = MagicMock()
    spider.seed_manager.add_urls_to_seeds.return_value = {"seed_inserted": 1}
    spider._add_urls_to_seeds(["https://a.example/next"], "https://a.example/js")
    assert spider.seed_manager.add_urls_to_seeds.call_args.kwargs["enqueue_stage2"] is True
