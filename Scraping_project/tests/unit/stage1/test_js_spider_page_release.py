"""#452: every Playwright page handed to the JS spider is released."""

import asyncio
import random
from types import SimpleNamespace

import pytest
from scrapy.http import HtmlResponse, Request

from src.stage1.experimental import playwright_guard as pg
from src.stage1.experimental.js_spider import JavaScriptSpider
from src.stage1.experimental.playwright_guard import PageLedger


class FakeBrowser:
    """Counts live pages like Chromium renderer processes; enforces a hard cap."""

    def __init__(self, cap):
        self.cap = cap
        self.live = 0
        self.peak = 0

    def new_page(self, fail_route=False, fail_close=False):
        if self.live >= self.cap:
            raise RuntimeError("pool saturated")
        self.live += 1
        self.peak = max(self.peak, self.live)
        return FakePage(self, fail_route, fail_close)


class FakePage:
    def __init__(self, browser, fail_route=False, fail_close=False):
        self.browser = browser
        self.fail_route = fail_route
        self.fail_close = fail_close
        self.closed = False
        self.close_calls = 0

    def on(self, event, cb):
        pass

    async def route(self, pattern, handler):
        if self.fail_route:
            raise RuntimeError("Target page, context or browser has been closed")

    async def evaluate(self, js):
        await asyncio.sleep(0)
        return 1000

    async def wait_for_timeout(self, ms):
        await asyncio.sleep(0)

    def is_closed(self):
        return self.closed

    async def close(self):
        self.close_calls += 1
        if not self.closed:
            self.closed = True
            self.browser.live -= 1
        if self.fail_close:
            raise RuntimeError("close failed")


def _spider():
    s = JavaScriptSpider.__new__(JavaScriptSpider)
    s.page_ledger = PageLedger()
    s.rendered_count = 0
    s.completed_urls = []
    s._add_urls_to_seeds = lambda urls, src: None
    return s


def _response(page, url="https://js.example.edu/app"):
    body = b'<html><body><a href="/a">a</a><a href="/b">b</a></body></html>'
    return HtmlResponse(url=url, body=body, encoding="utf-8",
                        request=Request(url, meta={"playwright_page": page}))


async def _drain(agen):
    return [item async for item in agen]


async def test_page_closed_before_first_item_on_success():
    browser = FakeBrowser(cap=4)
    spider = _spider()
    page = browser.new_page()
    agen = spider.parse(_response(page))
    first = await agen.__anext__()
    assert first["discovered_via_js"] is True
    assert page.closed and browser.live == 0  # released before yielding
    await agen.aclose()  # abandoning the generator leaks nothing
    assert spider.page_ledger.open_count == 0


async def test_exception_mid_render_still_closes_page(monkeypatch):
    browser = FakeBrowser(cap=4)
    spider = _spider()
    page = browser.new_page(fail_route=True)
    with pytest.raises(RuntimeError):
        await _drain(spider.parse(_response(page)))
    assert page.closed and page.close_calls == 1 and browser.live == 0


async def test_close_failure_does_not_mask_or_leak():
    browser = FakeBrowser(cap=4)
    spider = _spider()
    page = browser.new_page(fail_close=True)
    items = await _drain(spider.parse(_response(page)))
    assert items and spider.page_ledger.open_count == 0


async def test_errback_closes_page():
    browser = FakeBrowser(cap=4)
    spider = _spider()
    page = browser.new_page()
    spider.page_ledger.acquired(page)
    failure = SimpleNamespace(
        request=Request("https://js.example.edu/x", meta={"playwright_page": page}),
        getErrorMessage=lambda: "net::ERR_TIMED_OUT",
    )
    await spider.handle_error(failure)
    assert page.closed and browser.live == 0 and spider.page_ledger.open_count == 0


async def test_errback_without_page_is_safe():
    spider = _spider()
    failure = SimpleNamespace(request=Request("https://js.example.edu/x"), getErrorMessage=lambda: "dns")
    await spider.handle_error(failure)


async def test_chaos_exception_storm_does_not_grow_page_count():
    """200 renders, ~half failing mid-render, under a hard cap of 4 pages:
    the cap is never exceeded and every page is released."""
    rng = random.Random(452)
    browser = FakeBrowser(cap=4)
    spider = _spider()
    sem = asyncio.Semaphore(4)  # scrapy-playwright's PLAYWRIGHT_MAX_PAGES_PER_CONTEXT

    async def render(i):
        async with sem:
            page = browser.new_page(fail_route=rng.random() < 0.5, fail_close=rng.random() < 0.1)
            try:
                await _drain(spider.parse(_response(page, f"https://js.example.edu/{i}")))
            except RuntimeError:
                pass

    await asyncio.gather(*(render(i) for i in range(200)))
    assert browser.live == 0
    assert browser.peak <= 4
    assert spider.page_ledger.open_count == 0


def test_open_pages_gauge_tracks_ledger():
    if pg.OPEN_PAGES is None:
        pytest.skip("prometheus_client unavailable")
    ledger = PageLedger()
    browser = FakeBrowser(cap=2)
    p1, p2 = browser.new_page(), browser.new_page()
    ledger.acquired(p1)
    ledger.acquired(p2)
    assert pg.OPEN_PAGES._value.get() == 2
    asyncio.run(ledger.release(p1, "parse"))
    assert pg.OPEN_PAGES._value.get() == 1
    asyncio.run(ledger.release(p2, "errback"))
    assert pg.OPEN_PAGES._value.get() == 0


def test_hard_caps_configured():
    cs = JavaScriptSpider.custom_settings
    assert cs["PLAYWRIGHT_MAX_CONTEXTS"] >= 1
    assert cs["PLAYWRIGHT_MAX_PAGES_PER_CONTEXT"] >= 1
