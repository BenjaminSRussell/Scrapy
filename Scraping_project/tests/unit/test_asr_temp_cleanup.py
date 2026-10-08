"""ASR media temp files never leak and downloads are size-capped (#468)."""

from __future__ import annotations

import http.server
import threading
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool

import pytest
import requests

pytest.importorskip("twisted")

from prometheus_client import REGISTRY  # noqa: E402

from src.common import async_asr_processor as asr  # noqa: E402


@pytest.fixture(autouse=True)
def _allow_loopback_test_servers(monkeypatch):
    # The local test servers live on 127.0.0.1; the SSRF guard (#450) blocks
    # loopback unless FETCH_ALLOWED_CIDRS opts it in.
    monkeypatch.setenv("FETCH_ALLOWED_CIDRS", "127.0.0.0/8,::1/128")

PAYLOAD = b"RIFF" + b"\x00" * 50_000


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence test output
        pass

    def do_GET(self):
        if self.path.startswith("/missing"):
            self.send_response(404)
            self.end_headers()
            return
        if self.path.startswith("/truncated"):
            # Promise more bytes than we send, then drop the connection.
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD) * 4))
            self.end_headers()
            self.wfile.write(PAYLOAD[:1000])
            self.wfile.flush()
            self.connection.close()
            return
        if self.path.startswith("/chunked"):
            # No Content-Length: only the streaming counter can enforce the cap.
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for start in range(0, len(PAYLOAD), 8192):
                part = PAYLOAD[start : start + 8192]
                self.wfile.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)


@pytest.fixture(scope="module")
def media_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


class _FakeExecutor:
    """Stands in for the ProcessPoolExecutor so tests control the outcome."""

    def __init__(self, result=None, exc=None, submit_exc=None):
        self.result, self.exc, self.submit_exc = result, exc, submit_exc
        self.submitted = []

    def submit(self, fn, path):
        if self.submit_exc is not None:
            raise self.submit_exc
        self.submitted.append(path)
        fut: Future = Future()
        if self.exc is not None:
            fut.set_exception(self.exc)
        else:
            fut.set_result(self.result)
        return fut

    def shutdown(self, wait=True):
        pass


@pytest.fixture
def make_processor(tmp_path):
    made = []

    def _make(max_download_bytes=asr.DEFAULT_MAX_DOWNLOAD_BYTES, executor=None):
        proc = asr.AsyncASRProcessor(max_workers=1, temp_dir=str(tmp_path), max_download_bytes=max_download_bytes)
        proc.executor.shutdown(wait=False)
        proc.executor = executor or _FakeExecutor(result={"success": True, "transcript": "hi", "duration": 1.0})
        made.append(proc)
        return proc

    yield _make


def _files(tmp_path):
    return sorted(p.name for p in tmp_path.iterdir())


def _metric(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


# --- download ---------------------------------------------------------------


def test_successful_download_returns_complete_file(make_processor, media_server, tmp_path):
    proc = make_processor()
    path = proc._download_media(f"{media_server}/ok.wav")
    with open(path, "rb") as fh:
        assert fh.read() == PAYLOAD
    assert path.endswith(".wav")
    assert _files(tmp_path) == [path.rsplit("/", 1)[1]]


def test_http_error_leaves_no_temp_file(make_processor, media_server, tmp_path):
    proc = make_processor()
    before = _metric("scrapy_asr_downloads_rejected_total", reason="download_error")
    with pytest.raises(requests.HTTPError):
        proc._download_media(f"{media_server}/missing.wav")
    assert _files(tmp_path) == []
    assert _metric("scrapy_asr_downloads_rejected_total", reason="download_error") == before + 1


def test_connection_dropped_mid_stream_leaves_no_temp_file(make_processor, media_server, tmp_path):
    proc = make_processor()
    with pytest.raises(requests.RequestException):
        proc._download_media(f"{media_server}/truncated.wav")
    assert _files(tmp_path) == []


def test_unreachable_host_leaves_no_temp_file(make_processor, tmp_path):
    proc = make_processor()
    with pytest.raises(requests.RequestException):
        proc._download_media("http://127.0.0.1:9/nothing.wav")
    assert _files(tmp_path) == []


def test_declared_content_length_over_cap_is_rejected(make_processor, media_server, tmp_path):
    proc = make_processor(max_download_bytes=len(PAYLOAD) - 1)
    before = _metric("scrapy_asr_downloads_rejected_total", reason="too_large")
    with pytest.raises(asr.MediaTooLargeError, match="declares"):
        proc._download_media(f"{media_server}/ok.wav")
    assert _files(tmp_path) == []
    assert _metric("scrapy_asr_downloads_rejected_total", reason="too_large") == before + 1


def test_streamed_bytes_over_cap_are_rejected_without_content_length(make_processor, media_server, tmp_path):
    proc = make_processor(max_download_bytes=20_000)
    with pytest.raises(asr.MediaTooLargeError, match="exceeded"):
        proc._download_media(f"{media_server}/chunked.wav")
    assert _files(tmp_path) == []


def test_file_exactly_at_cap_is_accepted(make_processor, media_server, tmp_path):
    proc = make_processor(max_download_bytes=len(PAYLOAD))
    path = proc._download_media(f"{media_server}/chunked.wav")
    with open(path, "rb") as fh:
        assert fh.read() == PAYLOAD


@pytest.mark.parametrize("cap", [None, 0])
def test_cap_can_be_disabled(make_processor, media_server, cap):
    proc = make_processor(max_download_bytes=cap)
    assert proc.max_download_bytes is None
    proc._download_media(f"{media_server}/chunked.wav")


# --- transcription ----------------------------------------------------------


def _resolve(deferred):
    out = {}
    deferred.addCallbacks(lambda r: out.setdefault("ok", r), lambda f: out.setdefault("err", f))
    return out


def test_successful_transcription_removes_file(make_processor, media_server, tmp_path):
    proc = make_processor()
    path = proc._download_media(f"{media_server}/ok.wav")
    before = _metric("scrapy_asr_temp_files_total", outcome="cleaned")
    out = _resolve(proc._transcribe_async(path, {}))
    assert out["ok"]["transcript"] == "hi"
    assert _files(tmp_path) == []
    assert _metric("scrapy_asr_temp_files_total", outcome="cleaned") == before + 1


def test_failed_transcription_result_removes_file(make_processor, media_server, tmp_path):
    proc = make_processor(executor=_FakeExecutor(result={"success": False, "error": "garbled"}))
    path = proc._download_media(f"{media_server}/ok.wav")
    out = _resolve(proc._transcribe_async(path, {}))
    assert out["ok"]["transcription_error"] == "garbled"
    assert _files(tmp_path) == []


def test_worker_crash_still_removes_file(make_processor, media_server, tmp_path):
    """Previously os.remove sat inside the try after result(): a crash leaked it."""
    proc = make_processor(executor=_FakeExecutor(exc=BrokenProcessPool("worker died")))
    path = proc._download_media(f"{media_server}/ok.wav")
    out = _resolve(proc._transcribe_async(path, {}))
    assert out["err"].check(BrokenProcessPool)
    assert _files(tmp_path) == []


def test_malformed_result_still_removes_file(make_processor, media_server, tmp_path):
    proc = make_processor(executor=_FakeExecutor(result={"unexpected": True}))
    path = proc._download_media(f"{media_server}/ok.wav")
    out = _resolve(proc._transcribe_async(path, {}))
    assert out["err"].check(KeyError)
    assert _files(tmp_path) == []


def test_submit_to_shut_down_executor_removes_file(make_processor, media_server, tmp_path):
    proc = make_processor(executor=_FakeExecutor(submit_exc=RuntimeError("cannot schedule new futures")))
    path = proc._download_media(f"{media_server}/ok.wav")
    with pytest.raises(RuntimeError):
        proc._transcribe_async(path, {})
    assert _files(tmp_path) == []


def test_unremovable_file_is_counted_as_leaked(monkeypatch, tmp_path):
    victim = tmp_path / "stuck.wav"
    victim.write_bytes(b"x")

    def deny(path):
        raise PermissionError("read-only")

    monkeypatch.setattr(asr.os, "remove", deny)
    before = _metric("scrapy_asr_temp_files_total", outcome="leaked")
    assert asr.remove_temp_file(str(victim)) is False
    assert _metric("scrapy_asr_temp_files_total", outcome="leaked") == before + 1


def test_already_missing_file_counts_as_removed(tmp_path):
    assert asr.remove_temp_file(str(tmp_path / "gone.wav")) is True
    assert asr.remove_temp_file(None) is True


# --- end to end through process_media_url ----------------------------------


@pytest.fixture
def sync_pipeline(monkeypatch):
    """Run the download "thread" inline and pretend speech_recognition exists."""
    from twisted.internet import defer

    monkeypatch.setattr(asr, "SPEECH_RECOGNITION_AVAILABLE", True)
    monkeypatch.setattr(asr.threads, "deferToThread", lambda fn, *a: defer.maybeDeferred(fn, *a))


@pytest.mark.parametrize(
    "path,executor",
    [
        ("/missing.wav", None),
        ("/truncated.wav", None),
        ("/ok.wav", _FakeExecutor(exc=BrokenProcessPool("boom"))),
        ("/ok.wav", _FakeExecutor(submit_exc=RuntimeError("shut down"))),
        ("/ok.wav", None),
    ],
)
def test_process_media_url_never_leaves_temp_files(
    sync_pipeline, make_processor, media_server, tmp_path, path, executor
):
    proc = make_processor(executor=executor)
    item = {"url": "https://example.edu/page"}
    out = _resolve(proc.process_media_url(f"{media_server}{path}", item))
    assert "ok" in out, "errors are converted into a transcription_error field"
    assert _files(tmp_path) == []


def test_process_media_url_oversized_sets_error(sync_pipeline, make_processor, media_server, tmp_path):
    proc = make_processor(max_download_bytes=1000)
    out = _resolve(proc.process_media_url(f"{media_server}/ok.wav", {}))
    assert "MediaTooLargeError" in out["ok"]["transcription_error"]
    assert out["ok"]["transcript"] == ""
    assert _files(tmp_path) == []


def test_middleware_reads_settings(monkeypatch, tmp_path):
    from scrapy.settings import Settings

    class _Crawler:
        settings = Settings({"ASR_MAX_WORKERS": 1, "ASR_TEMP_DIR": str(tmp_path), "ASR_MAX_DOWNLOAD_BYTES": 1234})

        class signals:
            spider_closed = object()

            @staticmethod
            def connect(*a, **k):
                pass

    mw = asr.ASRMiddleware.from_crawler(_Crawler())
    try:
        assert mw.processor.temp_dir == str(tmp_path)
        assert mw.processor.max_download_bytes == 1234
    finally:
        mw.processor.executor.shutdown(wait=False)
