"""Dashboard cache headers (#969): HTML stays fresh; static assets can be cached."""
from __future__ import annotations

import importlib.util
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

DASH = Path(__file__).resolve().parents[2] / "dashboard"


@pytest.fixture(scope="module")
def serve():
    spec = importlib.util.spec_from_file_location("_cc_serve_cache_969", DASH / "serve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cache_control_matrix(serve):
    fresh = serve.NO_STORE
    short = serve.SHORT_CACHE
    immutable = serve.IMMUTABLE_CACHE
    assert "no-store" in fresh and "immutable" not in short
    for path in ("/", "/index.html", "/index.html?v=9", "/styleguide.html",
                 "/version.js", "/version.js?v=9", "/api/metrics", "/api/health",
                 "/api/queues?v=1", "/data.json", "/notes.txt"):
        assert serve.cache_control_for(path) == fresh, path
    for path in ("/app.js", "/features.js", "/format-utils.js", "/style.css",
                 "/favicon.ico", "/logo.svg", "/font.woff2", "/app.js.map",
                 "/app.js?v=", "/nested/app.js"):
        assert serve.cache_control_for(path) == short, path
        assert "no-store" not in serve.cache_control_for(path)
    for path in ("/app.a1b2c3d4.js", "/app.a1b2c3d4.min.js", "/theme-deadbeef01.css",
                 "/favicon.01234567.ico", "/app.js?v=3", "/app.js?version=1.2.3",
                 "/style.css?hash=abc", "/app.js?ver=2", "/app.js?V=9"):
        assert serve.cache_control_for(path) == immutable, path
        assert "no-store" not in serve.cache_control_for(path)


@pytest.fixture
def server(serve, tmp_path):
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html><title>CC</title>")
    (root / "app.js").write_text("console.log('ok');")
    (root / "style.css").write_text("body{}")
    (root / "app.a1b2c3d4.js").write_text("console.log('hashed');")
    (root / "favicon.ico").write_bytes(b"ico")
    (root / "data.json").write_text("{}")
    handler = type(
        "H",
        (serve.DashboardHandler,),
        {
            "root": root,
            "log_message": lambda *a: None,
            "auth": serve.AuthGate(),
            "limiter": serve.RateLimiter(per_minute=0),
            "cors_origins": (),
            "csp": serve.build_csp({}),
            "metrics_upstream": "http://127.0.0.1:9/metrics",
            "upstream_timeout": 0.3,
            "queue_keys": (),
        },
    )
    httpd = serve.make_server("127.0.0.1", 0, handler=handler, env={})
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def get(url, method="GET"):
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/", "no-store"),
        ("/index.html", "no-store"),
        ("/index.html?v=1", "no-store"),
        ("/version.js", "no-store"),
        ("/version.js?v=4", "no-store"),
        ("/api/health", "no-store"),
        ("/data.json", "no-store"),
        ("/app.js", "max-age=300"),
        ("/style.css", "max-age=300"),
        ("/favicon.ico", "max-age=300"),
        ("/app.js?v=", "max-age=300"),
        ("/app.a1b2c3d4.js", "immutable"),
        ("/app.js?v=3", "immutable"),
        ("/style.css?hash=abc", "immutable"),
        ("/missing.a1b2c3d4.js", "no-store"),
    ],
)
def test_served_cache_control(server, serve, path, expected):
    status, headers, _ = get(server + path)
    cache = headers["cache-control"]
    if expected == "no-store":
        assert cache == serve.NO_STORE
        if path.startswith("/missing"):
            assert status == 404
        elif path.startswith("/api/"):
            assert status == 200
        else:
            assert status == 200
    elif expected == "max-age=300":
        assert status == 200
        assert cache == serve.SHORT_CACHE
        assert "no-store" not in cache
    else:
        assert status == 200
        assert cache == serve.IMMUTABLE_CACHE
        assert "no-store" not in cache


def test_options_is_not_cached(server, serve):
    status, headers, _ = get(server + "/app.a1b2c3d4.js", method="OPTIONS")
    assert status == 204
    assert headers["cache-control"] == serve.NO_STORE
