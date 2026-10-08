"""#205: Stage 2 routes responses with Stage 1's content policy; PDFs never hit HTML analysis."""

from __future__ import annotations

import asyncio

import pytest
from multidict import CIMultiDict

from src.stage2.stage2_worker import Stage2Worker

ARTICLE = ("<html><head><title>T</title></head><body><p>" + "word " * 200 + "</p></body></html>").encode()
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\n" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
DOCX = b"PK\x03\x04" + b"\x00" * 64


class _Stream:
    def __init__(self, owner):
        self.owner = owner

    async def iter_chunked(self, n):
        self.owner.reads += 1
        body = self.owner._body
        for i in range(0, len(body), n):
            self.owner.bytes_read += len(body[i : i + n])
            yield body[i : i + n]


class FakeResponse:
    def __init__(self, body: bytes, content_type: str | None, status: int = 200, content_length=None, charset=None):
        self.status = status
        self.headers = CIMultiDict({} if content_type is None else {"Content-Type": content_type})
        self._body = body
        self.reads = 0
        self.bytes_read = 0
        self.content_length = content_length
        self.charset = charset
        self.content = _Stream(self)

    async def read(self) -> bytes:
        self.reads += 1
        self.bytes_read += len(self._body)
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response = response

    def get(self, url, **kwargs):
        return self.response


@pytest.fixture
def worker(monkeypatch):
    w = Stage2Worker.__new__(Stage2Worker)
    w.stage4_writes = []
    w.max_body_bytes = 1024 * 1024
    w.stage4_pdf_max_bytes = 50 * 1024 * 1024
    w.html_analysed = []

    class Delta:
        def write(self, table, rows, mode="append", async_write=True):
            w.stage4_writes.append((table, rows))
            return True

    w.delta = Delta()

    async def analyze_html(url, url_hash, html, is_heavy):
        w.html_analysed.append(html)
        return {"url": url, "url_hash": url_hash, "analysed": True}

    monkeypatch.setattr(w, "_analyze_html", analyze_html)

    class NoBan:
        def detect(self, *a, **k):
            return None

    monkeypatch.setattr(w, "_detector", lambda: NoBan())
    return w


def fetch(worker, body, content_type, **kw):
    response = FakeResponse(body, content_type, **kw)
    record = asyncio.run(
        worker._fetch_once(FakeSession(response), "https://x.uconn.edu/doc", "h", False, "x.uconn.edu")
    )
    return record, response


@pytest.mark.parametrize(
    "body, ctype",
    [
        (ARTICLE, "text/html; charset=utf-8"),
        (ARTICLE, "TEXT/HTML ; Charset=UTF-8"),
        (ARTICLE, "application/xhtml+xml"),  # was a minimal record before
        (ARTICLE, None),  # header-less HTML is sniffed, as in Stage 1
    ],
)
def test_html_goes_to_the_analyzer(worker, body, ctype):
    record, _ = fetch(worker, body, ctype)
    assert record.get("analysed") is True
    assert worker.stage4_writes == []


@pytest.mark.parametrize("ctype", ["application/pdf", "application/PDF; qs=0.9"])
def test_pdf_header_routes_to_stage4_without_downloading_the_body(worker, ctype):
    record, response = fetch(worker, PDF, ctype)
    assert record["is_pdf"] is True and record["routed_to_stage4"] is True
    assert [t for t, _ in worker.stage4_writes] == ["stage4_large_docs"]
    assert response.reads == 0
    assert worker.html_analysed == []


@pytest.mark.parametrize("ctype", ["text/html", "text/html; charset=utf-8", None])
def test_pdf_body_never_enters_the_html_analyzer(worker, ctype):
    record, _ = fetch(worker, PDF, ctype)
    assert worker.html_analysed == []  # was analysed as HTML under text/html before
    assert record["routed_to_stage4"] is True
    assert [t for t, _ in worker.stage4_writes] == ["stage4_large_docs"]


@pytest.mark.parametrize(
    "body, ctype, expected_type",
    [
        (b'{"a": 1}', "application/json", "application/json"),
        (PNG, "image/png", "image/png"),
        (
            DOCX,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (PNG, "text/html", "text/html"),  # mislabeled binary
        (b"just some text", None, "missing_content_type"),
    ],
)
def test_other_content_gets_a_minimal_record(worker, body, ctype, expected_type):
    record, response = fetch(worker, body, ctype)
    assert worker.html_analysed == []
    assert worker.stage4_writes == []
    assert record["title"] == "Binary/Other Content"
    assert record["content_type"] == expected_type
    if ctype and not ctype.startswith("text/html"):
        assert response.reads == 0  # decided from the header alone


# --- size caps (#248) -------------------------------------------------------


def _oversized(kind):
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value("stage2_oversized_skipped_total", {"kind": kind}) or 0.0


def test_html_over_the_declared_cap_is_skipped_without_reading(worker):
    before = _oversized("html")
    record, response = fetch(worker, ARTICLE, "text/html", content_length=5 * 1024 * 1024)
    assert record["content_type"] == "oversized:html"
    assert record.get("has_error") is not True  # recorded, not retried
    assert response.reads == 0 and worker.html_analysed == []
    assert _oversized("html") == before + 1


def test_html_without_content_length_stops_reading_at_the_cap(worker):
    worker.max_body_bytes = 200 * 1024
    big = b"<html><body>" + b"x" * (2 * 1024 * 1024) + b"</body></html>"
    record, response = fetch(worker, big, "text/html")
    assert record["content_type"] == "oversized:html"
    assert response.bytes_read <= 200 * 1024 + 64 * 1024  # stopped within one chunk
    assert worker.html_analysed == []


def test_pdf_over_the_stage4_cap_is_not_queued(worker):
    before = _oversized("pdf")
    record, _ = fetch(worker, PDF, "application/pdf", content_length=60 * 1024 * 1024)
    assert record["content_type"] == "oversized:pdf"
    assert worker.stage4_writes == []
    assert _oversized("pdf") == before + 1
    # Under the cap (or unknown size): queued as before.
    record, _ = fetch(worker, PDF, "application/pdf", content_length=1024)
    assert record["routed_to_stage4"] is True


def test_zero_cap_disables_the_limit(worker):
    worker.max_body_bytes = 0
    record, _ = fetch(worker, ARTICLE, "text/html", content_length=10**9)
    assert record.get("analysed") is True


def test_declared_charset_is_used_for_decoding(worker):
    body = "<html><body><p>caf\u00e9 ".encode("latin-1") + b"word " * 60 + b"</p></body></html>"
    fetch(worker, body, "text/html; charset=iso-8859-1", charset="iso-8859-1")
    assert "caf\u00e9" in worker.html_analysed[0]


def test_cap_comes_from_config_and_env(monkeypatch):
    from src.stage2.stage2_worker import Stage2Worker

    w = Stage2Worker.__new__(Stage2Worker)
    monkeypatch.delenv("STAGE2_MAX_BODY_BYTES", raising=False)
    assert w._max_body_bytes() == 20 * 1024 * 1024  # committed config.yml
    w2 = Stage2Worker.__new__(Stage2Worker)
    monkeypatch.setenv("STAGE2_MAX_BODY_BYTES", "1234")
    assert w2._max_body_bytes() == 1234
