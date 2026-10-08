"""#445: PDF extraction runs under memory/time/size budgets in a child process."""

import asyncio
import sys
import time
from datetime import datetime

import pytest

from src.stage4 import pdf_sandbox as sb
from src.stage4.pdf_sandbox import PdfQuarantined, extract_pdf_text
from src.stage4.stage4_worker import QUEUE_TABLE, Stage4Worker
from src.utils.delta import DeltaHelper


def _minimal_pdf(text: str) -> bytes:
    """A valid one-page PDF with a text object (correct xref offsets)."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def _child(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def _counter(c):
    return c._value.get() if c is not None else 0


def test_real_pdf_extracted_in_child():
    text = extract_pdf_text(_minimal_pdf("Hello Sandbox"), max_rss_mb=1024, timeout_s=60)
    assert "Hello Sandbox" in text


def test_garbage_pdf_is_parse_error():
    with pytest.raises(PdfQuarantined) as e:
        extract_pdf_text(b"%PDF-1.4 this is not a pdf", timeout_s=60)
    assert e.value.reason == "parse_error"


def test_oversized_pdf_rejected_before_spawning(monkeypatch):
    def no_spawn(*a, **k):
        raise AssertionError("must not spawn a child")

    monkeypatch.setattr(sb.subprocess, "run", no_spawn)
    with pytest.raises(PdfQuarantined) as e:
        extract_pdf_text(b"x" * 101, max_bytes=100)
    assert e.value.reason == "too_large"


def test_memory_budget_kills_child_not_parent():
    """Simulated oversized PDF: the child blows through its RLIMIT_AS budget
    (via the real apply_limits) and the parent carries on."""
    before = _counter(sb.STAGE4_OCR_OOM)
    code = (
        "import os\n"
        "from src.stage4.pdf_extract_child import apply_limits, EXIT_OOM\n"
        "apply_limits()\n"
        "try:\n"
        "    blob = bytearray(1024 * 1024 * 1024)\n"
        "except MemoryError:\n"
        "    os._exit(EXIT_OOM)\n"
    )
    with pytest.raises(PdfQuarantined) as e:
        extract_pdf_text(b"%PDF", max_rss_mb=256, timeout_s=60, argv=_child(code))
    assert e.value.reason == "oom"
    if sb.STAGE4_OCR_OOM is not None:
        assert _counter(sb.STAGE4_OCR_OOM) == before + 1


def test_sigkilled_child_counts_as_oom():
    code = "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"
    with pytest.raises(PdfQuarantined) as e:
        extract_pdf_text(b"%PDF", timeout_s=60, argv=_child(code))
    assert e.value.reason == "oom"


def test_time_budget_kills_child():
    before = _counter(sb.STAGE4_OCR_TIMEOUT)
    start = time.monotonic()
    with pytest.raises(PdfQuarantined) as e:
        extract_pdf_text(b"%PDF", timeout_s=0.5, argv=_child("import time; time.sleep(30)"))
    assert e.value.reason == "timeout"
    assert time.monotonic() - start < 10
    if sb.STAGE4_OCR_TIMEOUT is not None:
        assert _counter(sb.STAGE4_OCR_TIMEOUT) == before + 1


def test_missing_pdf_library_is_transient_not_quarantine():
    with pytest.raises(RuntimeError, match="no PDF library"):
        extract_pdf_text(b"%PDF", timeout_s=60, argv=_child("import sys; sys.exit(4)"))


def test_fetch_content_does_not_retry_quarantined_pdf(monkeypatch):
    from src.stage4 import large_doc_processor as ldp

    proc = ldp.LargeDocProcessor.__new__(ldp.LargeDocProcessor)
    calls = {"get": 0}

    class Resp:
        headers = {"Content-Type": "application/pdf"}
        content = b"%PDF"

        def raise_for_status(self):
            pass

    def get(url):
        calls["get"] += 1
        return Resp()

    proc.http_client = type("C", (), {"get": staticmethod(get), "close": lambda self: None})()

    def boom(data):
        raise PdfQuarantined("oom")

    monkeypatch.setattr(ldp, "extract_pdf_text", boom)
    with pytest.raises(PdfQuarantined):
        proc._fetch_content("https://x.edu/a.pdf", is_pdf=True)
    assert calls["get"] == 1  # no tenacity retries for a deterministic failure


def test_worker_marks_quarantined_and_moves_on(tmp_path):
    class Proc:
        def __init__(self):
            self.fetched = []

        def _fetch_content(self, url, is_pdf=False):
            self.fetched.append(url)
            if url == "bomb.pdf":
                raise PdfQuarantined("oom", "rc=-9")
            return "text " * 10, "pdf"

        def process_large_document(self, url, text):
            return "summary"

    w = Stage4Worker.__new__(Stage4Worker)
    w.delta = DeltaHelper(base_path=tmp_path / "lake")
    w.processor = Proc()
    for url in ("bomb.pdf", "ok.pdf"):
        w.delta.write(QUEUE_TABLE, [{
            "url": url, "url_hash": f"h-{url}", "word_count": 0, "content_length": 0,
            "status": "pending", "is_pdf": True, "queued_at": datetime.now().isoformat(),
        }], mode="append", async_write=False)

    assert asyncio.run(w._run_traced()) == 1
    status = {r["url"]: r["status"] for r in w.delta.read(QUEUE_TABLE)}
    assert status == {"bomb.pdf": "quarantined:oom", "ok.pdf": "completed"}

    w.processor.fetched.clear()
    assert asyncio.run(w._run_traced()) == 0
    assert w.processor.fetched == []  # never re-attempted: no requeue-forever loop
