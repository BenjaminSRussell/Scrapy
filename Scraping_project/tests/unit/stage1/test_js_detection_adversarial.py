"""Adversarial HTML for JS detection (#295).

Fixtures live in tests/fixtures/js_detection/. The false-positive cases are pages that
*talk about* SPA frameworks in their prose or code samples. Before the fix those strings
counted as framework/async/state evidence even though the page is fully server-rendered,
so Stage 1 sent tutorials and docs to Playwright for nothing.
"""

from pathlib import Path

import pytest
from scrapy.http import HtmlResponse, Request

from src.stage1.js_detection import JSDetector

pytestmark = [pytest.mark.unit, pytest.mark.stage1]

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "js_detection"


def _detect(name: str) -> dict:
    url = f"https://example.edu/{name}"
    body = (FIXTURES / name).read_bytes()
    response = HtmlResponse(url=url, body=body, encoding="utf-8", request=Request(url=url))
    return JSDetector(response).requires_js_rendering()


NEEDS_JS = {
    "cra_noscript_shell.html": None,
    "vue_cloak_shell.html": "vue",
    "next_shell_big_inline_state.html": "next.js",
    "nuxt_uppercase_single_quotes.html": "nuxt",
}

STATIC = [
    "react_tutorial_article.html",
    "angular_tutorial_article.html",
    "static_docs_jquery_analytics.html",
    "image_only_no_scripts.html",
]


def test_fixture_set_is_large_enough():
    assert len(NEEDS_JS) + len(STATIC) >= 5
    assert {p.name for p in FIXTURES.glob("*.html")} == set(NEEDS_JS) | set(STATIC)


@pytest.mark.parametrize("name,framework", sorted(NEEDS_JS.items()))
def test_spa_shells_need_js(name, framework):
    result = _detect(name)
    assert result["requires_js"] is True, result
    if framework:
        assert result["detected_framework"] == framework


@pytest.mark.parametrize("name", STATIC)
def test_server_rendered_pages_do_not_need_js(name):
    result = _detect(name)
    assert result["requires_js"] is False, result


@pytest.mark.parametrize("name", ["react_tutorial_article.html", "angular_tutorial_article.html"])
def test_framework_names_in_prose_or_code_are_not_evidence(name):
    result = _detect(name)
    assert result["detected_framework"] is None, result
    assert not any("state object" in r.lower() or "async" in r.lower() for r in result["reasons"]), result


def test_large_inline_state_json_does_not_hide_an_empty_shell():
    # body ::text includes the 5 KB __NEXT_DATA__ script, so "minimal content" alone misses it;
    # the empty-body check (scripts stripped) must still fire.
    result = _detect("next_shell_big_inline_state.html")
    assert any("empty body" in r.lower() for r in result["reasons"]), result


def test_markup_only_haystack_keeps_script_and_attribute_evidence():
    html = '<html><body><div data-reactroot=""></div><script src="/react-dom.production.min.js"></script></body></html>'
    url = "https://example.edu/x"
    det = JSDetector(HtmlResponse(url=url, body=html.encode(), encoding="utf-8", request=Request(url=url)))
    assert det._detect_spa_framework() == {"detected": True, "framework": "react"}
