"""#385: URLExtractor decode/parse failures are counted and logged, never silently swallowed."""

import ast
import base64
import logging
from pathlib import Path

import pytest
from prometheus_client import REGISTRY
from scrapy.http import HtmlResponse

from src.stage1.processors.url_extractor import URLExtractor

SRC = Path(__file__).resolve().parents[2] / "src" / "stage1" / "processors" / "url_extractor.py"
BASE = "https://uconn.edu/"
TARGET = "https://uconn.edu/hidden/page"


def _metric(source):
    return REGISTRY.get_sample_value("scrapy_url_extractor_decode_failures_total", {"source": source}) or 0.0


def _discover(body: str) -> set[str]:
    resp = HtmlResponse(url=BASE, body=f"<html><body>{body}</body></html>".encode(), encoding="utf-8")
    return URLExtractor(BASE, ["uconn.edu"]).discover_all_urls(resp)


def _script(js: str) -> str:
    return f"<script>{js}</script>"


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


# ------------------------------------------------------------------ atob
@pytest.mark.parametrize(
    "encoded",
    [b64(TARGET.encode()), b64(TARGET.encode()).rstrip("="), " ".join(b64(TARGET.encode()))],
    ids=["padded", "unpadded", "whitespace"],
)
def test_atob_valid_base64_is_discovered(encoded):
    assert TARGET in _discover(_script(f'var u = atob("{encoded}");'))


@pytest.mark.parametrize(
    "encoded,why",
    [("!!not*base64!!", "bad alphabet"), ("abcde", "impossible length"), (b64(b"\xff\xfe\xfa/x"), "not utf-8")],
)
def test_atob_invalid_is_counted_and_logged(encoded, why, caplog):
    before = _metric("atob")
    with caplog.at_level(logging.DEBUG, logger="src.stage1.processors.url_extractor"):
        found = _discover(_script(f'x = atob("{encoded}");'))
    assert not any("hidden" in u for u in found)
    assert _metric("atob") == before + 1, why
    assert any("atob decode failed" in r.getMessage() and encoded[:10] in r.getMessage() for r in caplog.records)


def test_atob_payload_in_log_is_truncated(caplog):
    junk = "*" * 5000
    with caplog.at_level(logging.DEBUG, logger="src.stage1.processors.url_extractor"):
        _discover(_script(f'atob("{junk}")'))
    msgs = [r.getMessage() for r in caplog.records if "atob decode failed" in r.getMessage()]
    assert msgs and all(len(m) < 400 for m in msgs)


def test_atob_valid_but_not_a_url_is_ignored_without_counting():
    before = _metric("atob")
    _discover(_script(f'atob("{b64(b"just some words")}")'))
    assert _metric("atob") == before


# ---------------------------------------------------- decodeURIComponent
def test_decode_uri_component_is_discovered():
    assert TARGET in _discover(_script('go(decodeURIComponent("https%3A%2F%2Fuconn.edu%2Fhidden%2Fpage"));'))


def test_decode_uri_component_malformed_utf8_is_counted(caplog):
    before = _metric("uri")
    with caplog.at_level(logging.DEBUG, logger="src.stage1.processors.url_extractor"):
        _discover(_script('decodeURIComponent("https%3A%2F%2Fuconn.edu%2F%E0%A4%A")'))
    assert _metric("uri") == before + 1
    assert any("uri decode failed" in r.getMessage() for r in caplog.records)


def test_decode_uri_component_argument_is_not_treated_as_base64():
    # Base64 of a URL inside decodeURIComponent() is not a URL in the browser either.
    assert TARGET not in _discover(_script(f'decodeURIComponent("{b64(TARGET.encode())}")'))


def test_unescape_uses_latin1_and_never_fails():
    before = _metric("uri")
    found = _discover(_script('unescape("https%3A//uconn.edu/caf%E9")'))
    assert "https://uconn.edu/caf\u00e9" in found
    assert _metric("uri") == before


# --------------------------------------------------------------- json-ld
def test_malformed_json_ld_is_counted_and_valid_still_parsed():
    before = _metric("json_ld")
    bad = '<script type="application/ld+json">{"url": "https://uconn.edu/a",</script>'
    good = '<script type="application/ld+json">{"@type": "Thing", "url": "https://uconn.edu/from-ld"}</script>'
    found = _discover(bad + good)
    assert _metric("json_ld") == before + 1
    assert "https://uconn.edu/from-ld" in found


# --------------------------------------------------------------- urljoin
def test_malformed_href_is_counted_not_crashing():
    before = _metric("urljoin")
    found = _discover('<a href="http://[::1/broken">x</a><a href="/ok">ok</a>')
    assert "https://uconn.edu/ok" in found
    assert _metric("urljoin") == before + 1


# ------------------------------------------------------------- hygiene
def test_no_bare_or_silent_broad_excepts():
    tree = ast.parse(SRC.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        assert node.type is not None, f"bare except at line {node.lineno}"
        silent = all(isinstance(stmt, ast.Pass) for stmt in node.body)
        broad = isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}
        assert not (broad and silent), f"silent `except Exception: pass` at line {node.lineno}"
