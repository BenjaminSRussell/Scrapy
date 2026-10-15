"""Dashboard SSE live metrics (#656): parsed push, poll is unchanged, auth stays on the gate."""
from __future__ import annotations

import importlib.util
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
import pytest

DASH = Path(__file__).resolve().parents[2] / "dashboard"
FIXTURE = """\
# comment
errors_total{stage="s1"} 3
errors_total{stage="s2"} 4
pipeline_running 1
plain_total 2
plain_total{k="v"} 9
up{job="x"} +Inf
not a metric
"""


@pytest.fixture(scope="module")
def serve():
    spec = importlib.util.spec_from_file_location("_cc_serve_sse", DASH / "serve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_metrics_text_matches_dashboard_rules(serve):
    parsed = serve.parse_metrics_text(FIXTURE)
    assert parsed["errors_total"] == 7
    assert parsed['errors_total{stage="s1"}'] == 3
    assert parsed["pipeline_running"] == 1
    assert parsed["plain_total"] == 2  # unlabelled wins over the labelled sibling
    assert parsed['plain_total{k="v"}'] == 9
    assert "up" not in parsed and not any(k.startswith("up{") for k in parsed)
    assert serve.parse_metrics_text("") == {}


def test_resolve_sse_interval(serve):
    assert serve.resolve_sse_interval(2.0, None) == 2.0
    assert serve.resolve_sse_interval(0.05, None) == 0.05
    assert serve.resolve_sse_interval(2.0, "5") == 5.0
    assert serve.resolve_sse_interval(2.0, "0.1") == 0.5  # query floor
    assert serve.resolve_sse_interval(2.0, "5000") == 5.0  # milliseconds
    assert serve.resolve_sse_interval(2.0, "nope") == 2.0
    assert serve.resolve_sse_interval(2.0, "90") == 60.0
    assert serve.resolve_sse_interval(2.0, "90000") == 60.0
    assert serve.resolve_sse_interval(2.0, "999") == pytest.approx(0.999)
    assert serve.resolve_sse_interval(0.05, "1") == 1.0
    assert serve.sse_enabled_from_env({}) is True
    assert serve.sse_enabled_from_env({"CC_SSE": "0"}) is False
    assert serve.sse_enabled_from_env({"CC_SSE": "off"}) is False
    assert serve.sse_max_from_env({"CC_SSE_MAX": "0"}) == 32
    assert serve.sse_max_from_env({"CC_SSE_MAX": "4"}) == 4


def _handler(serve, root, env):
    return type(
        "H",
        (serve.DashboardHandler,),
        {
            "root": root,
            "log_message": lambda *a: None,
            "auth": serve.AuthGate.from_env(env),
            "limiter": serve.RateLimiter.from_env(env),
            "cors_origins": serve.parse_cors_origins(env),
            "csp": serve.build_csp(env),
            "metrics_upstream": env.get("METRICS_UPSTREAM", serve.DEFAULT_METRICS_UPSTREAM),
            "upstream_timeout": float(env.get("CC_UPSTREAM_TIMEOUT", 4)),
            "queue_keys": serve.queue_keys(env),
            "sse_enabled": serve.sse_enabled_from_env(env),
            "sse_interval": serve.sse_interval_from_env(env),
            "sse_max_clients": serve.sse_max_from_env(env),
        },
    )


@pytest.fixture
def make_server(serve, tmp_path):
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html><title>CC</title>")
    servers = []

    def start(env):
        handler = _handler(serve, root, env)
        httpd = serve.make_server("127.0.0.1", 0, handler=handler, env=env)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return httpd, handler

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def _open(httpd, path, headers=None, timeout=3.0):
    host, port = httpd.server_address
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    req = f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n"
    for key, value in (headers or {}).items():
        req += f"{key}: {value}\r\n"
    sock.sendall((req + "\r\n").encode())
    return sock


def _event_json(data: bytes, name: str) -> dict:
    marker = f"event: {name}\ndata: ".encode()
    start = data.find(marker)
    assert start >= 0, data
    line = data[start + len(marker):].split(b"\n", 1)[0].strip()
    return json.loads(line)


def _read_until(sock, predicate, timeout=3.0):
    data = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            piece = sock.recv(4096)
        except socket.timeout:
            break
        if not piece:
            break
        data += piece
        if predicate(data):
            break
    return data


def _status(data: bytes) -> int:
    return int(data.split(b"\r\n", 1)[0].split()[1])


def test_stream_pushes_parsed_metrics(make_server, serve):
    def opener(req, timeout=4.0):  # noqa: ARG001
        class R:
            def read(self, n=-1):
                return FIXTURE.encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    orig = serve.fetch_upstream
    serve.fetch_upstream = lambda url, timeout=4.0, opener=opener: orig(url, timeout, opener)
    try:
        httpd, _ = make_server({"CC_SSE_INTERVAL": "0.05", "CC_RATE_LIMIT": "0", "METRICS_UPSTREAM": "http://exporter/metrics"})
        sock = _open(httpd, "/api/metrics/stream")
        try:
            data = _read_until(sock, lambda buf: b"event: metrics" in buf)
        finally:
            sock.close()
        assert _status(data) == 200
        headers = data.split(b"\r\n\r\n", 1)[0].decode().lower()
        assert "content-type: text/event-stream" in headers
        assert "x-accel-buffering: no" in headers
        assert "transfer-encoding: chunked" in headers
        payload = _event_json(data, "metrics")
        assert payload["pipeline_running"] == 1
        assert payload["errors_total"] == 7
        assert payload["plain_total"] == 2
        host, port = httpd.server_address
        with urllib.request.urlopen(f"http://{host}:{port}/api/metrics", timeout=3) as resp:
            assert resp.status == 200
            assert b"pipeline_running 1" in resp.read()
    finally:
        time.sleep(0.2)
        serve.fetch_upstream = orig


def test_stream_reports_upstream_and_stays_a_stream(make_server, serve):
    def opener(req, timeout=4.0):  # noqa: ARG001
        raise urllib.error.URLError("down")

    orig = serve.fetch_upstream
    serve.fetch_upstream = lambda url, timeout=4.0, opener=opener: orig(url, timeout, opener)
    try:
        httpd, _ = make_server({"CC_SSE_INTERVAL": "0.05", "CC_RATE_LIMIT": "0"})
        sock = _open(httpd, "/api/metrics/stream")
        try:
            data = _read_until(sock, lambda buf: b"event: upstream" in buf)
        finally:
            sock.close()
        assert _status(data) == 200
        assert b'"error":"URLError"' in data
        assert b"event: metrics" not in data
    finally:
        time.sleep(0.2)
        serve.fetch_upstream = orig


def test_stream_disabled_is_404_not_a_stream(make_server):
    httpd, _ = make_server({"CC_SSE": "0", "CC_RATE_LIMIT": "0"})
    sock = _open(httpd, "/api/metrics/stream")
    try:
        data = _read_until(sock, lambda buf: b"SSE disabled" in buf)
    finally:
        sock.close()
    assert _status(data) == 404
    assert b"event:" not in data


def test_stream_requires_the_same_auth_gate(make_server, serve):
    def opener(req, timeout=4.0):  # noqa: ARG001
        class R:
            def read(self, n=-1):
                return b"pipeline_running 1\n"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    orig = serve.fetch_upstream
    serve.fetch_upstream = lambda url, timeout=4.0, opener=opener: orig(url, timeout, opener)
    try:
        httpd, _ = make_server({"CC_AUTH_TOKEN": "tok-xyz", "CC_SSE_INTERVAL": "0.05", "CC_RATE_LIMIT": "0"})
        denied = _open(httpd, "/api/metrics/stream")
        try:
            data = _read_until(denied, lambda buf: b"Authentication required" in buf)
        finally:
            denied.close()
        assert _status(data) == 401
        assert b"event:" not in data
        allowed = _open(httpd, "/api/metrics/stream", headers={"Authorization": "Bearer tok-xyz"})
        try:
            data = _read_until(allowed, lambda buf: b"event: metrics" in buf)
        finally:
            allowed.close()
        assert _status(data) == 200
        assert b'"pipeline_running":1' in data or b'"pipeline_running":1.0' in data
    finally:
        time.sleep(0.2)
        serve.fetch_upstream = orig


def test_stream_cap_returns_503(make_server, serve):
    def opener(req, timeout=4.0):  # noqa: ARG001
        class R:
            def read(self, n=-1):
                return b"pipeline_running 1\n"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    orig = serve.fetch_upstream
    serve.fetch_upstream = lambda url, timeout=4.0, opener=opener: orig(url, timeout, opener)
    try:
        httpd, handler = make_server({"CC_SSE_INTERVAL": "0.05", "CC_RATE_LIMIT": "0", "CC_SSE_MAX": "1"})
        assert handler.sse_max_clients == 1
        first = _open(httpd, "/api/metrics/stream")
        try:
            held = _read_until(first, lambda buf: b"event: metrics" in buf)
            assert _status(held) == 200
            second = _open(httpd, "/api/metrics/stream")
            try:
                data = _read_until(second, lambda buf: b"Too many live metric streams" in buf)
            finally:
                second.close()
            assert _status(data) == 503
            assert b"Retry-After: 5" in data or b"retry-after: 5" in data.lower()
        finally:
            first.close()
    finally:
        time.sleep(0.2)
        serve.fetch_upstream = orig


def test_readme_documents_sse_auth():
    text = (DASH / "README.md").read_text()
    assert "/api/metrics/stream" in text
    assert "EventSource" in text
    assert "query string" in text
    assert "CC_SSE" in text
    assert "HTTP Basic" in text
    assert "## Live metrics (SSE) (#656)" in text
