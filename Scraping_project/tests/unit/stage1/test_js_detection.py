"""Unit tests for JavaScript detection heuristics."""

from scrapy.http import HtmlResponse, Request

from src.stage1.js_detection import JSDetector, detect_js_requirement


def build_response(body: str, url: str = "https://example.com") -> HtmlResponse:
    request = Request(url=url)
    return HtmlResponse(url=url, body=body.encode("utf-8"), encoding="utf-8", request=request)


def test_detects_spa_framework_confidence():
    html = """
    <html>
      <body>
        <div id="__next"></div>
        <script src="/_next/static/runtime.js"></script>
        <script>
          window.__NEXT_DATA__ = {};
        </script>
      </body>
    </html>
    """
    response = build_response(html)
    detector = JSDetector(response)

    result = detector.requires_js_rendering()

    assert result["requires_js"] is True
    assert result["detected_framework"] == "next.js"
    assert any("bundled app" in reason.lower() for reason in result["reasons"])


def test_returns_false_for_static_page():
    html = """
    <html>
      <body>
        <h1>Hello</h1>
        <p>This page has no dynamic behaviour.</p>
      </body>
    </html>
    """
    response = build_response(html)
    assert detect_js_requirement(response) is False


def test_spa_root_selector_prioritises_known_ids():
    html = """
    <html>
      <body>
        <div id="app">Loading…</div>
      </body>
    </html>
    """
    response = build_response(html)
    detector = JSDetector(response)

    assert detector.get_spa_root_selector() == "#app"
