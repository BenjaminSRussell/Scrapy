"""src/stage2/intelligent_analyzer.py: offline unit suite (#221).

No network, no Redis, no Delta: the HTTP client, lake and thresholds are injected.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import httpx
import pytest

from src.stage2.intelligent_analyzer import IntelligentAnalyzer, _is_pdf_link, analyze_url

TH = SimpleNamespace(min_word_count=50, min_text_to_html_ratio=0.1, massive_doc_threshold=2000)


class FakeLake:
    def __init__(self, fail=False):
        self.writes: list[tuple[str, list, dict]] = []
        self.fail = fail

    def write(self, table, rows, **kw):
        if self.fail:
            raise OSError("lake down")
        self.writes.append((table, rows, kw))


def make(handler=None, lake=None, th=TH):
    transport = httpx.MockTransport(handler or (lambda req: httpx.Response(200, text="")))
    return IntelligentAnalyzer(client=httpx.Client(transport=transport), delta=lake or FakeLake(), thresholds=th)


def html_page(words: int, extra_markup: str = "", links: str = "") -> str:
    body = " ".join(f"word{i}" for i in range(words))
    return f"<html><head><style>p{{}}</style><script>var x=1;</script></head><body><nav>menu menu</nav>" \
           f"<p>{body}</p>{extra_markup}{links}<footer>foot</footer></body></html>"


@pytest.fixture(autouse=True)
def no_yake(monkeypatch):
    """Keyword extraction is covered separately; keep the suite deterministic and fast."""
    monkeypatch.setattr(IntelligentAnalyzer, "_extract_keywords", lambda self, text, heavy: ["kw"] if text else [])


# --- HTML quality gates -------------------------------------------------------


def test_good_page_is_scored_and_boilerplate_stripped():
    r = make()._analyze_html("https://u.edu/a", html_page(200), False)
    assert r["word_count"] == 200  # nav/footer/script/style removed
    assert "menu" not in r["text_extracted"] and "var x" not in r["text_extracted"]
    assert r["is_low_quality"] is False and r["is_massive_doc"] is False
    assert r["keywords"] == ["kw"]
    assert r["quality_score"] == pytest.approx(min(200 / 1000, 0.6) + min(r["text_to_html_ratio"] * 0.4, 0.4), abs=1e-3)


def test_empty_body_is_low_quality_with_zero_ratio():
    r = make()._analyze_html("https://u.edu/a", "", False)
    assert (r["word_count"], r["content_length"], r["html_length"], r["text_to_html_ratio"]) == (0, 0, 0, 0)
    assert r["is_low_quality"] is True and r["text_extracted"] == "" and r["keywords"] == []
    assert r["quality_score"] == 0


@pytest.mark.parametrize("words,low", [(49, True), (50, False)])
def test_min_word_count_edge(words, low):
    assert make()._analyze_html("https://u.edu/a", html_page(words), False)["is_low_quality"] is low


def test_markup_heavy_page_fails_the_text_to_html_ratio():
    bloat = "<div class='x'></div>" * 2000
    r = make()._analyze_html("https://u.edu/a", html_page(200, extra_markup=bloat), False)
    assert r["text_to_html_ratio"] < TH.min_text_to_html_ratio
    assert r["is_low_quality"] is True


def test_massive_doc_is_routed_to_stage4_once_and_skips_keywords():
    lake = FakeLake()
    r = make(lake=lake)._analyze_html("https://u.edu/big", html_page(1000), False)
    assert r["is_massive_doc"] is True and r["keywords"] == []
    (table, rows, kw), = lake.writes
    assert table == "stage4_large_docs" and rows[0]["url"] == "https://u.edu/big"
    assert rows[0]["status"] == "pending" and rows[0]["word_count"] == 1000
    assert kw == {"mode": "append", "async_write": True}


def test_massive_but_low_quality_doc_is_not_routed():
    lake = FakeLake()
    bloat = "<span></span>" * 50000
    r = make(lake=lake)._analyze_html("https://u.edu/big", html_page(1000, extra_markup=bloat), False)
    assert r["is_massive_doc"] is True and r["is_low_quality"] is True
    assert lake.writes == []


def test_stage4_routing_failure_does_not_fail_the_analysis():
    r = make(lake=FakeLake(fail=True))._analyze_html("https://u.edu/big", html_page(1000), False)
    assert r["has_error"] is False and r["is_massive_doc"] is True


def test_pdf_links_by_extension_case_and_query():
    links = ('<a href="/a.pdf">a</a><a href="/B.PDF">b</a><a href="/c.pdf?dl=1#p2">c</a>'
             '<a href="/view?file=d.pdf">d</a><a href="/e.pdfx">e</a><a>no href</a>')
    r = make()._analyze_html("https://u.edu/a", html_page(100, links=links), False)
    assert r["pdf_links"] == ["/a.pdf", "/B.PDF", "/c.pdf?dl=1#p2"]
    assert r["has_pdf"] is True


@pytest.mark.parametrize("href,ok", [("x.pdf", True), ("X.Pdf", True), ("a.pdf#x", True), ("?f=a.pdf", False),
                                     ("a.pdf.html", False), ("http://[bad/a.pdf", False)])
def test_is_pdf_link(href, ok):
    assert _is_pdf_link(href) is ok


@pytest.mark.parametrize("words,score", [(0, 0.0), (500, 0.5), (600, 0.6), (5000, 0.6)])
def test_quality_score_word_component_caps_at_0_6(words, score):
    assert make()._calculate_quality_score(words, 0.0) == score


@pytest.mark.parametrize("ratio,score", [(0.0, 0.0), (0.5, 0.2), (1.0, 0.4), (3.0, 0.4)])
def test_quality_score_ratio_component_caps_at_0_4(ratio, score):
    assert make()._calculate_quality_score(0, ratio) == score


# --- analyze(): routing by status / content type ----------------------------


def test_http_errors_become_error_records():
    a = make(lambda req: httpx.Response(404))
    r = a.analyze("https://u.edu/missing")
    assert r["has_error"] is True and r["is_404"] is True and r["error_code"] == 404
    assert r["is_low_quality"] is True and r["quality_score"] == 0
    assert make(lambda req: httpx.Response(503)).analyze("https://u.edu/x")["is_404"] is False


@pytest.mark.parametrize("exc,msg", [(httpx.ReadTimeout("t"), "timeout"), (httpx.ConnectError("c"), "connection_failed"),
                                     (RuntimeError("boom"), "unknown: boom")])
def test_transport_failures_are_classified(exc, msg):
    def handler(req):
        raise exc

    r = make(handler).analyze("https://u.edu/x")
    assert r["has_error"] is True and r["error_code"] == 0 and r["error_message"] == msg


def test_html_and_text_content_types_use_the_html_path():
    for ctype in ("text/html; charset=utf-8", "application/xhtml+xml", "text/plain"):
        a = make(lambda req: httpx.Response(200, text=html_page(80), headers={"content-type": ctype}))
        assert a.analyze("https://u.edu/a")["word_count"] == 80


def test_pdf_path_with_stub_reader(monkeypatch):
    class Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    class Reader:
        def __init__(self, fp):
            self.pages = [Page("alpha " * 120), Page(None), Page("beta " * 30)]  # image-only middle page

    monkeypatch.setitem(sys.modules, "PyPDF2", types.SimpleNamespace(PdfReader=Reader))
    a = make(lambda req: httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"}))
    r = a.analyze("https://u.edu/doc.pdf")
    assert r["has_pdf"] is True and r["has_ocr"] is False
    assert r["word_count"] == 150  # a None page no longer discards the document
    assert r["is_low_quality"] is False and r["keywords"] == ["kw"]
    assert r["quality_score"] == pytest.approx(0.15 + 0.4)


def test_pdf_massive_threshold_counts_characters_like_html(monkeypatch):
    """1200 words x ~8 chars = ~9.6k chars > 2000-char threshold: massive (was compared to words)."""
    class Reader:
        def __init__(self, fp):
            self.pages = [types.SimpleNamespace(extract_text=lambda: "abcdefg " * 1200)]

    monkeypatch.setitem(sys.modules, "PyPDF2", types.SimpleNamespace(PdfReader=Reader))
    r = make()._analyze_pdf("https://u.edu/doc.pdf", b"%PDF", False)
    assert r["word_count"] == 1200 < TH.massive_doc_threshold
    assert r["is_massive_doc"] is True


def test_unreadable_pdf_without_ocr_is_low_quality(monkeypatch):
    def broken(fp):
        raise ValueError("not a pdf")

    monkeypatch.setitem(sys.modules, "PyPDF2", types.SimpleNamespace(PdfReader=broken))
    monkeypatch.setitem(sys.modules, "easyocr", None)  # import fails -> OCR skipped
    r = make()._analyze_pdf("https://u.edu/doc.pdf", b"junk", False)
    assert r["word_count"] == 0 and r["is_low_quality"] is True and r["has_ocr"] is False


def test_binary_non_image_has_consistent_shape():
    r = make(lambda req: httpx.Response(200, content=b"\x00\x01", headers={"content-type": "application/zip"})) \
        .analyze("https://u.edu/a.zip")
    assert r["is_404"] is False and r["has_error"] is False
    assert r["word_count"] == 0 and r["is_low_quality"] is True and r["keywords"] == []


def test_analyze_url_closes_its_client(monkeypatch):
    closed = []
    monkeypatch.setattr(IntelligentAnalyzer, "__init__", lambda self: setattr(self, "client", None))
    monkeypatch.setattr(IntelligentAnalyzer, "analyze", lambda self, url, heavy: {"url": url})
    monkeypatch.setattr(IntelligentAnalyzer, "close", lambda self: closed.append(True))
    assert analyze_url("https://u.edu/a") == {"url": "https://u.edu/a"}
    assert closed == [True]


def test_real_keyword_extraction_short_text_and_limits(monkeypatch):
    monkeypatch.undo()  # use the real _extract_keywords
    pytest.importorskip("yake")
    a = make()
    assert a._extract_keywords("too short", False) == []
    text = ("The University of Connecticut research computing center supports machine learning and "
            "high performance computing for faculty research projects across campus. ") * 5
    assert 0 < len(a._extract_keywords(text, False)) <= 10
    assert len(a._extract_keywords(text, True)) <= 20
