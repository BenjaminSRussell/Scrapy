"""src/stage2/intelligent_analyzer.py: offline unit suite (#221).

No network, no Redis, no Delta: the HTTP client, lake and thresholds are injected.
"""

from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace

import httpx
import pytest

from src.stage2.intelligent_analyzer import (
    IntelligentAnalyzer,
    _is_pdf_link,
    _load_scholarly_parser,
    analyze_url,
    extract_citations,
)

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


# --- citations (#651) ---------------------------------------------------------


_PUBLICATION_HTML = """
<html><head>
  <meta name="citation_doi" content="10.1000/PAGE">
  <meta name="citation_title" content="Page Paper">
  <meta name="citation_author" content="Ada Lovelace">
  <meta name="citation_author" content="Alan Turing">
  <meta name="citation_journal_title" content="J. Test">
  <meta name="citation_publication_date" content="2019/05/01">
</head><body>
  <p>See https://doi.org/10.1038/nature12373 and doi:10.5555/from-text.</p>
  <p>Trailing (10.1016/j.cell.2020.01.001).</p>
  <p>Again 10.1000/PAGE in the abstract.</p>
</body></html>
"""


def test_doi_detection_from_meta_url_and_prose():
    cites = extract_citations(_PUBLICATION_HTML)
    by_doi = {row["doi"].lower(): row for row in cites}
    assert set(by_doi) == {
        "10.1000/page",
        "10.1038/nature12373",
        "10.5555/from-text",
        "10.1016/j.cell.2020.01.001",
    }
    page = by_doi["10.1000/page"]
    assert page["source"] == "meta"
    assert page["doi"] == "10.1000/PAGE"
    assert page["title"] == "Page Paper"
    assert page["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert page["journal"] == "J. Test"
    assert page["year"] == "2019"
    encoded = json.dumps(cites)
    assert encoded.startswith("[") and json.loads(encoded) == cites


@pytest.mark.parametrize("raw,doi", [
    ("10.1000/182", "10.1000/182"),
    ("doi:10.1000/182", "10.1000/182"),
    ("https://doi.org/10.1000/182", "10.1000/182"),
    ("https://dx.doi.org/10.1000/AbC.", "10.1000/AbC"),
    ("(10.1000/182).", "10.1000/182"),
    ("before 110.1000/not-a-doi", None),
    ("10.123/too-short", None),
])
def test_doi_detection_shapes(raw, doi):
    found = [row["doi"] for row in extract_citations(text=raw)]
    assert found == ([doi] if doi else [])


@pytest.mark.parametrize("html,text", [
    (None, None),
    ("", ""),
    ("   ", None),
    ("<html><body>no identifiers here</body></html>", ""),
    ("<p>not a doi 10.12/x", ""),
])
def test_citation_extraction_is_empty_safe(html, text):
    assert extract_citations(html, text) == []
    assert extract_citations(html=object(), text=object()) == []


def test_short_and_error_pages_store_an_empty_or_meta_list():
    empty = make()._analyze_html("https://u.edu/a", "", False)
    assert empty["citations"] == []
    missing = make(lambda req: httpx.Response(404)).analyze("https://u.edu/missing")
    assert missing["citations"] == []
    binary = make(lambda req: httpx.Response(200, content=b"\x00\x01", headers={"content-type": "application/zip"})) \
        .analyze("https://u.edu/a.zip")
    assert binary["citations"] == []
    short = '<html><head><meta name="citation_doi" content="10.5555/short"></head><body><p>Hi</p></body></html>'
    analyzed = make()._analyze_html("https://u.edu/p", short, False)
    assert analyzed["is_low_quality"] is True
    assert analyzed["citations"] == [{
        "doi": "10.5555/short",
        "title": "",
        "authors": [],
        "journal": "",
        "year": "",
        "raw": "10.5555/short",
        "source": "meta",
    }]
    assert json.loads(json.dumps(analyzed["citations"])) == analyzed["citations"]


def test_pdf_text_dois_are_stored(monkeypatch):
    class Reader:
        def __init__(self, fp):
            self.pages = [types.SimpleNamespace(extract_text=lambda: ("word " * 80) + "See 10.5555/pdf.")]

    monkeypatch.setitem(sys.modules, "PyPDF2", types.SimpleNamespace(PdfReader=Reader))
    result = make()._analyze_pdf("https://u.edu/doc.pdf", b"%PDF", False)
    assert [row["doi"] for row in result["citations"]] == ["10.5555/pdf"]
    assert result["citations"][0]["source"] == "doi"


def test_scholarly_parser_is_optional(monkeypatch):
    loaded = _load_scholarly_parser()
    assert loaded is None or callable(loaded)
    monkeypatch.setattr("src.stage2.intelligent_analyzer._load_scholarly_parser", lambda: None)
    assert [row["doi"] for row in extract_citations(text="10.1000/182")] == ["10.1000/182"]


def test_optional_scholarly_parser_merges_and_failures_stay_empty_safe(monkeypatch):
    def parse(text):
        assert "10.1000/keep" in text
        return [
            {"doi": "10.9999/from-parser", "title": "Parsed title", "authors": ["Lin"], "year": 2021, "journal": "Proc"},
            {"doi": "10.1000/keep", "title": "duplicate of the regex hit"},
            "no doi in this string",
            {"title": "", "authors": []},
            3,
            None,
        ]

    monkeypatch.setattr("src.stage2.intelligent_analyzer._load_scholarly_parser", lambda: parse)
    cites = extract_citations("<p>Keep 10.1000/keep in the page.</p>")
    by_doi = {row["doi"].lower(): row for row in cites}
    assert set(by_doi) == {"10.1000/keep", "10.9999/from-parser"}
    assert by_doi["10.1000/keep"]["source"] == "doi"
    parsed = by_doi["10.9999/from-parser"]
    assert parsed["source"] == "scholarly"
    assert parsed["title"] == "Parsed title" and parsed["authors"] == ["Lin"]
    assert parsed["year"] == "2021" and parsed["journal"] == "Proc"

    def broken(text):
        raise RuntimeError("parser down")

    monkeypatch.setattr("src.stage2.intelligent_analyzer._load_scholarly_parser", lambda: broken)
    assert [row["doi"] for row in extract_citations(text="10.1000/still-here")] == ["10.1000/still-here"]
    monkeypatch.setattr("src.stage2.intelligent_analyzer._load_scholarly_parser", lambda: (lambda text: None))
    assert extract_citations(text="no identifiers") == []


def test_real_keyword_extraction_short_text_and_limits(monkeypatch):
    monkeypatch.undo()  # use the real _extract_keywords
    pytest.importorskip("yake")
    a = make()
    assert a._extract_keywords("too short", False) == []
    text = ("The University of Connecticut research computing center supports machine learning and "
            "high performance computing for faculty research projects across campus. ") * 5
    assert 0 < len(a._extract_keywords(text, False)) <= 10
    assert len(a._extract_keywords(text, True)) <= 20
