"""Optional JS-spider screenshots (#630): flag, failure path, retention, gitignore."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from scrapy.http import HtmlResponse, Request

from src.stage1.experimental import js_screenshots as shots
from src.stage1.experimental.js_spider import JavaScriptSpider
from src.stage1.experimental.playwright_guard import PageLedger
from src.utils.url_canon import url_hash

pytestmark = [pytest.mark.unit, pytest.mark.stage1]

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
URL = "https://js.example.edu/app"


class _Page:
    def __init__(self, *, closed=False, payload=PNG, raises=False, accept_options=True):
        self.closed = closed
        self.payload = payload
        self.raises = raises
        self.accept_options = accept_options
        self.shot_calls = 0
        self.close_calls = 0
        self.events: list[str] = []

    def is_closed(self):
        return self.closed

    async def screenshot(self, path, **kwargs):
        if not self.accept_options and kwargs:
            raise TypeError("unexpected keywords")
        self.shot_calls += 1
        self.events.append("shot")
        if self.raises:
            raise RuntimeError("screenshot timed out")
        Path(path).write_bytes(self.payload)

    async def close(self):
        self.close_calls += 1
        self.events.append("close")
        self.closed = True

    def on(self, event, cb):
        return None

    async def route(self, pattern, handler):
        return None

    async def evaluate(self, js):
        return 1000

    async def wait_for_timeout(self, ms):
        return None


def _spider():
    spider = JavaScriptSpider.__new__(JavaScriptSpider)
    spider.page_ledger = PageLedger()
    spider.rendered_count = 0
    spider.completed_urls = []
    spider.failed_urls = {}
    spider._add_urls_to_seeds = lambda urls, src: None
    return spider


def _response(page, url=URL, body=b"<html><body></body></html>"):
    return HtmlResponse(
        url=url,
        body=body,
        encoding="utf-8",
        request=Request(url, meta={"playwright_page": page}),
    )


async def _drain(agen):
    return [item async for item in agen]


def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv(shots.FLAG, raising=False)
    assert shots.screenshots_enabled() is False


def test_retention_env_overrides_config(monkeypatch):
    monkeypatch.setenv(shots.RETENTION_ENV, "3")
    monkeypatch.setenv(shots.MAX_FILES_ENV, "0")
    assert shots.retention_days() == 3
    assert shots.max_files() == 0


def test_retention_rejects_bad_env_and_reads_config(monkeypatch):
    monkeypatch.setenv(shots.RETENTION_ENV, "nope")
    assert shots.retention_days() == shots.DEFAULT_RETENTION_DAYS

    monkeypatch.delenv(shots.RETENTION_ENV, raising=False)
    monkeypatch.delenv(shots.MAX_FILES_ENV, raising=False)

    class _Cfg:
        def get(self, key, default=None):
            values = {
                shots.RETENTION_CONFIG: 11,
                shots.MAX_FILES_CONFIG: 4,
            }
            return values.get(key, default)

    monkeypatch.setattr("src.core.config.get_config", lambda: _Cfg())
    assert shots.retention_days() == 11
    assert shots.max_files() == 4


def test_shipped_config_keeps_screenshot_flag_off():
    project = Path(__file__).resolve().parents[3]
    text = (project / "config.yml").read_text(encoding="utf-8")
    assert "js_screenshots_enabled: false" in text
    assert "js_screenshot_retention_days: 7" in text
    assert "js_screenshot_max_files: 200" in text


async def test_disabled_does_not_write_or_call_screenshot(tmp_path):
    page = _Page()
    path = await shots.capture_page_screenshot(page, URL, directory=tmp_path, enabled=False)
    assert path is None
    assert page.shot_calls == 0
    assert list(tmp_path.iterdir()) == []


async def test_success_writes_url_hash_png(tmp_path):
    page = _Page()
    path = await shots.capture_page_screenshot(
        page, URL, directory=tmp_path, enabled=True, retention_days=7, max_files=200
    )
    assert path == tmp_path / f"{url_hash(URL)}.png"
    assert path is not None and path.read_bytes().startswith(b"\x89PNG")
    assert page.shot_calls == 1
    assert list(tmp_path.glob("*.part")) == []


async def test_non_png_is_discarded(tmp_path):
    page = _Page(payload=b"<html>nope</html>")
    path = await shots.capture_page_screenshot(page, URL, directory=tmp_path, enabled=True)
    assert path is None
    assert list(tmp_path.glob("*.png")) == []
    assert list(tmp_path.glob("*.part")) == []


async def test_closed_page_and_screenshot_error_are_safe(tmp_path):
    closed = _Page(closed=True)
    assert await shots.capture_page_screenshot(closed, URL, directory=tmp_path, enabled=True) is None
    assert closed.shot_calls == 0

    broken = _Page(raises=True)
    assert await shots.capture_page_screenshot(broken, URL, directory=tmp_path, enabled=True) is None
    assert list(tmp_path.glob("*.png")) == []


async def test_path_only_screenshot_callable_still_works(tmp_path):
    page = _Page(accept_options=False)
    path = await shots.capture_page_screenshot(page, URL, directory=tmp_path, enabled=True, max_files=10)
    assert path is not None and path.is_file()


def test_retention_drops_old_and_caps_newest(tmp_path):
    now = time.time()
    for index in range(5):
        png = tmp_path / f"{index:016x}.png"
        png.write_bytes(PNG)
        mtime = now - (index * 86400)
        os.utime(png, (mtime, mtime))
    fresh = tmp_path / f"{url_hash(URL)}.png"
    fresh.write_bytes(PNG)
    removed = shots.prune_screenshots(tmp_path, retention_days=2, max_files=2, now=now, keep=fresh)
    names = sorted(p.name for p in tmp_path.glob("*.png"))
    assert fresh.name in names
    assert len(names) == 2
    assert removed >= 3


def test_just_written_file_survives_age_and_cap(tmp_path):
    now = time.time()
    fresh = tmp_path / f"{url_hash(URL)}.png"
    fresh.write_bytes(PNG)
    os.utime(fresh, (now - 10 * 86400, now - 10 * 86400))
    newer = tmp_path / f"{'b' * 16}.png"
    newer.write_bytes(PNG)
    os.utime(newer, (now, now))
    shots.prune_screenshots(tmp_path, retention_days=1, max_files=1, now=now, keep=fresh)
    assert fresh.exists()
    assert not newer.exists()


def test_zero_limits_keep_every_png(tmp_path):
    old = tmp_path / f"{'a' * 16}.png"
    old.write_bytes(PNG)
    os.utime(old, (time.time() - 30 * 86400, time.time() - 30 * 86400))
    removed = shots.prune_screenshots(tmp_path, retention_days=0, max_files=0, keep=old)
    assert removed == 0 and old.exists()


def test_screenshots_are_gitignored():
    repo = Path(__file__).resolve().parents[4]
    project_ignore = (repo / "Scraping_project" / ".gitignore").read_text(encoding="utf-8")
    root_ignore = (repo / ".gitignore").read_text(encoding="utf-8")
    assert "data/screenshots/" in project_ignore
    assert "Scraping_project/data/screenshots/" in root_ignore
    rel = f"Scraping_project/data/screenshots/{url_hash(URL)}.png"
    result = subprocess.run(["git", "check-ignore", "-q", rel], cwd=repo, check=False)
    assert result.returncode == 0


async def test_spider_success_captures_empty_render_before_release(tmp_path, monkeypatch):
    monkeypatch.setenv(shots.FLAG, "1")
    monkeypatch.setattr(shots, "screenshot_directory", lambda base=None: tmp_path)
    page = _Page()
    spider = _spider()
    items = await _drain(spider.parse(_response(page)))
    assert items == []
    assert page.events == ["shot", "close"]
    assert (tmp_path / f"{url_hash(URL)}.png").is_file()
    assert spider.rendered_count == 1


async def test_spider_flag_off_skips_screenshot(tmp_path, monkeypatch):
    monkeypatch.setenv(shots.FLAG, "0")
    monkeypatch.setattr(shots, "screenshot_directory", lambda base=None: tmp_path)
    page = _Page()
    spider = _spider()
    body = b'<html><body><a href="/a">a</a></body></html>'
    items = await _drain(spider.parse(_response(page, body=body)))
    assert items and items[0]["discovered_via_js"] is True
    assert page.events == ["close"]
    assert list(tmp_path.glob("*.png")) == []


async def test_errback_captures_then_releases(tmp_path, monkeypatch):
    monkeypatch.setenv(shots.FLAG, "1")
    monkeypatch.setattr(shots, "screenshot_directory", lambda base=None: tmp_path)
    page = _Page()
    spider = _spider()
    spider.page_ledger.acquired(page)
    failure = SimpleNamespace(
        request=Request(URL, meta={"playwright_page": page}),
        getErrorMessage=lambda: "net::ERR_TIMED_OUT",
    )
    await spider.handle_error(failure)
    assert page.events == ["shot", "close"]
    assert spider.page_ledger.open_count == 0
    assert (tmp_path / f"{url_hash(URL)}.png").is_file()
    assert spider.failed_urls[URL].startswith("net::ERR_TIMED_OUT")


async def test_errback_screenshot_error_still_releases(tmp_path, monkeypatch):
    monkeypatch.setenv(shots.FLAG, "1")
    monkeypatch.setattr(shots, "screenshot_directory", lambda base=None: tmp_path)
    page = _Page(raises=True)
    spider = _spider()
    spider.page_ledger.acquired(page)
    failure = SimpleNamespace(
        request=Request(URL, meta={"playwright_page": page}),
        getErrorMessage=lambda: "navigation failed",
    )
    await spider.handle_error(failure)
    assert page.closed and spider.page_ledger.open_count == 0
    assert list(tmp_path.glob("*.png")) == []


async def test_errback_without_page_is_safe(monkeypatch):
    monkeypatch.setenv(shots.FLAG, "1")
    spider = _spider()
    failure = SimpleNamespace(request=Request(URL), getErrorMessage=lambda: "dns")
    await spider.handle_error(failure)
    assert spider.failed_urls[URL] == "dns"
