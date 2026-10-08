"""#390: Playwright resource blocking comes from config, per domain, from the first request."""

import asyncio
from types import SimpleNamespace

import pytest

from src.stage1.experimental import playwright_blocking as pb
from src.stage1.experimental.playwright_blocking import BlockPolicy

pytestmark = [pytest.mark.unit, pytest.mark.stage1]


class Cfg(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _req(rtype, page_url="https://www.example.edu/page", url="https://cdn.example.net/x"):
    return SimpleNamespace(resource_type=rtype, url=url, frame=SimpleNamespace(page=SimpleNamespace(url=page_url)))


def test_defaults_match_previous_hardcoded_list():
    p = BlockPolicy.from_config(Cfg())
    assert p.default == frozenset({"image", "stylesheet", "font", "media"})
    assert p.should_block("image", "https://a.edu/") and not p.should_block("script", "https://a.edu/")


def test_config_changes_blocked_types_without_code_edit():
    p = BlockPolicy.from_config(Cfg({"stage1.js_blocked_resource_types": ["media", "font"]}))
    assert p.should_block("media", "https://a.edu/")
    assert not p.should_block("image", "https://a.edu/")
    assert not BlockPolicy.from_config(Cfg({"stage1.js_blocked_resource_types": []})).should_block("image", "https://a.edu/")


def test_per_domain_override_matches_subdomains_and_most_specific_wins():
    p = BlockPolicy.from_config(
        Cfg(
            {
                "stage1.js_blocked_resource_types_by_domain": {
                    "example.edu": ["image"],
                    "catalog.example.edu": [],
                    ".Strict.org": ["image", "stylesheet", "font", "media", "xhr"],
                }
            }
        )
    )
    assert p.should_block("image", "https://www.example.edu/a")
    assert not p.should_block("stylesheet", "https://www.example.edu/a")  # override replaces default
    assert not p.should_block("image", "https://catalog.example.edu/c")  # more specific key
    assert p.should_block("xhr", "https://strict.org/")
    assert p.should_block("stylesheet", "https://notexample.edu/")  # not a subdomain: default applies
    assert p.should_block("stylesheet", "https://other.edu/")  # default applies


def test_document_is_never_blocked():
    p = BlockPolicy.from_config(Cfg({"stage1.js_blocked_resource_types": ["document", "image"]}))
    assert not p.should_block("document", "https://a.edu/")
    assert p.should_block("image", "https://a.edu/")


@pytest.mark.parametrize(
    "cfg",
    [
        {"stage1.js_blocked_resource_types": ["imagez"]},
        {"stage1.js_blocked_resource_types_by_domain": {"a.edu": ["nope"]}},
        {"stage1.js_blocked_resource_types_by_domain": ["a.edu"]},
        {"stage1.js_blocked_resource_types": 5},
    ],
)
def test_invalid_config_fails_fast(cfg):
    with pytest.raises(ValueError):
        BlockPolicy.from_config(Cfg(cfg))


def test_abort_hook_uses_page_domain_not_subresource_host(monkeypatch):
    policy = BlockPolicy.from_config(Cfg({"stage1.js_blocked_resource_types_by_domain": {"example.edu": []}}))
    monkeypatch.setattr(pb, "_POLICY", policy)
    assert pb.should_abort_request(_req("image")) is False  # page on example.edu: nothing blocked
    assert pb.should_abort_request(_req("image", page_url="https://other.org/")) is True
    assert pb.should_abort_request(_req("document", page_url="https://other.org/")) is False
    # Service-worker requests have no frame: fall back to the request URL.
    sw = SimpleNamespace(resource_type="image", url="https://x.example.edu/i.png")
    assert pb.should_abort_request(sw) is False


def test_shipped_config_loads_and_spider_registers_hook():
    from src.core.config import get_config
    from src.stage1.experimental.js_spider import JavaScriptSpider

    policy = BlockPolicy.from_config(get_config())
    assert policy.default == frozenset(pb.DEFAULT_BLOCKED_TYPES)
    hook = JavaScriptSpider.custom_settings["PLAYWRIGHT_ABORT_REQUEST"]
    from scrapy.utils.misc import load_object

    assert load_object(hook) is pb.should_abort_request


def test_in_page_route_uses_policy_and_falls_back(monkeypatch):
    from src.stage1.experimental.js_spider import JavaScriptSpider

    monkeypatch.setattr(pb, "_POLICY", BlockPolicy.from_config(Cfg({"stage1.js_blocked_resource_types": ["font"]})))
    handlers = []

    class Page:
        url = "https://a.edu/"

        async def route(self, pattern, handler):
            handlers.append(handler)

    class Route:
        def __init__(self, rtype):
            self.request = SimpleNamespace(resource_type=rtype)
            self.calls = []

        async def abort(self):
            self.calls.append("abort")

        async def fallback(self):
            self.calls.append("fallback")

    spider = JavaScriptSpider.__new__(JavaScriptSpider)
    asyncio.run(spider._setup_resource_blocking(Page()))
    font, img = Route("font"), Route("image")
    asyncio.run(handlers[0](font))
    asyncio.run(handlers[0](img))
    assert font.calls == ["abort"] and img.calls == ["fallback"]
