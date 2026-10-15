"""serve.py feed routes: snapshot, stage, url, explore and the SSE stream."""
from __future__ import annotations

import base64
import http.client
import importlib.util
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

import pytest

from ccfeed.demo import DemoFeed
from ccfeed.hub import Hub

DASH = Path(__file__).resolve().parents[3] / "dashboard"


@pytest.fixture(scope="module")
def serve():
    spec = importlib.util.spec_from_file_location("_cc_serve_feed", DASH / "serve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def start(serve, tmp_path):
    root = tmp_path / "static"
    (root / "ccfeed").mkdir(parents=True)
    (root / "tools").mkdir()
    (root / "index.html").write_text("<!doctype html><title>CC</title>")
    (root / "package.json").write_text('{"type": "module"}')
    (root / "ccfeed" / "hub.js").write_text("x")
    (root / "tools" / "smoke.js").write_text("x")
    servers, hubs = [], []

    def _start(env=None, max_streams=16):
        env = {"CC_RATE_LIMIT": "0"} if env is None else env

        def factory():
            hub = Hub(DemoFeed(seed=1), interval=0.2)
            hub.start()
            hubs.append(hub)
            return hub

        handler = type("H", (serve.DashboardHandler,), {
            "root": root, "log_message": lambda *a: None,
            "auth": serve.AuthGate.from_env(env), "limiter": serve.RateLimiter.from_env(env),
            "cors_origins": (), "csp": serve.build_csp(env), "metrics_upstream": "http://127.0.0.1:9/metrics",
            "upstream_timeout": 1.0, "queue_keys": (), "grafana_url": serve.DEFAULT_GRAFANA_URL,
            "hub_factory": staticmethod(factory), "_hub": None, "max_streams": max_streams,
            "stream_ping": 0.5,
        })
        httpd = serve.make_server("127.0.0.1", 0, handler=handler, env=env)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}", handler

    yield _start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()
    for hub in hubs:
        hub.stop()


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def get_json(url, headers=None):
    status, headers, body = get(url, headers)
    return status, json.loads(body or b"null")


def open_stream(base, headers=None):
    u = urlsplit(base)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
    conn.request("GET", "/api/stream", headers=headers or {})
    return conn, conn.getresponse()


def read_until(resp, kind, timeout=3.0):
    """Lines of the event stream up to and including the data line of ``event: kind``."""
    lines, deadline, want_data = [], time.time() + timeout, False
    while time.time() < deadline:
        line = resp.fp.readline().decode().rstrip("\n")
        lines.append(line)
        if line == f"event: {kind}":
            want_data = True
        elif want_data and line.startswith("data: "):
            return lines, json.loads(line[6:])
    raise AssertionError(f"no {kind} event in {lines[:20]}")


def test_snapshot_route_serves_the_hub(start):
    base, _ = start()
    status, headers, body = get(base + "/api/snapshot")
    assert status == 200 and headers["content-type"].startswith("application/json")
    snap = json.loads(body)
    assert snap["mode"] == "demo"
    assert [s["id"] for s in snap["stages"]] == ["seeds", "discovery", "analysis", "summaries", "large_docs"]


def test_stage_route_adds_the_live_stage_and_404s_unknown_ids(start):
    base, _ = start()
    status, detail = get_json(base + "/api/stage/discovery")
    assert status == 200
    assert detail["stage"]["id"] == "discovery" and detail["stage"]["count"] > 0
    assert detail["breakdowns"] and detail["recent"]
    assert get(base + "/api/stage/nope")[0] == 404


def test_url_route_rejects_non_http_and_404s_unknown(start):
    base, _ = start()
    assert get(base + "/api/url?u=" + quote("javascript:alert(1)"))[0] == 400
    assert get(base + "/api/url?u=" + quote("https://example.edu/" + "a" * 5000))[0] == 400
    assert get(base + "/api/url")[0] == 400
    assert get(base + "/api/url?u=" + quote("https://never.example/"))[0] == 404
    known = get_json(base + "/api/snapshot")[1]["recent"][0]["url"]
    status, journey = get_json(base + "/api/url?u=" + quote(known, safe=""))
    assert status == 200 and journey["url"] == known and len(journey["steps"]) == 5


def test_explore_route(start):
    base, _ = start()
    status, root = get_json(base + "/api/explore?path=")
    assert status == 200 and root["children"]
    host = root["children"][0]["segment"]
    assert get_json(base + "/api/explore?path=" + quote(host))[1]["path"] == [host]
    assert get(base + "/api/explore?path=nope.example")[0] == 404


def test_stream_sends_retry_then_snapshot_then_ticks(start):
    base, _ = start()
    conn, resp = open_stream(base)
    try:
        assert resp.status == 200
        assert resp.getheader("Content-Type").startswith("text/event-stream")
        assert resp.fp.readline() == b"retry: 3000\n"
        _, snap = read_until(resp, "snapshot")
        assert snap["mode"] == "demo"
        _, tick = read_until(resp, "tick")
        assert {"stages", "events", "series_point"} <= set(tick)
    finally:
        resp.close()
        conn.close()


def test_closing_the_stream_releases_its_subscriber(start):
    base, handler = start()
    conn, resp = open_stream(base)
    read_until(resp, "snapshot")
    assert handler._hub.subscriber_count == 1
    resp.close()  # the response holds the socket open too
    conn.close()
    deadline = time.time() + 3
    while handler._hub.subscriber_count and time.time() < deadline:
        time.sleep(0.05)
    assert handler._hub.subscriber_count == 0


def test_stream_cap_returns_503(start):
    base, _ = start(max_streams=1)
    conn, resp = open_stream(base)
    try:
        read_until(resp, "snapshot")
        status, _ = get_json(base + "/api/stream")
        assert status == 503
    finally:
        resp.close()
        conn.close()


def test_new_routes_require_auth_when_it_is_on(start):
    base, _ = start({"CC_RATE_LIMIT": "0", "CC_AUTH_TOKEN": "s3cret"})
    for path in ("/api/snapshot", "/api/stream", "/api/stage/discovery", "/api/explore?path=",
                 "/api/url?u=https%3A%2F%2Fa.edu%2F"):
        assert get(base + path)[0] == 401, path
    assert get(base + "/api/snapshot", {"Authorization": "Bearer s3cret"})[0] == 200
    basic = base64.b64encode(b"anyone:s3cret").decode()
    conn, resp = open_stream(base, {"Authorization": f"Basic {basic}"})
    try:
        assert resp.status == 200
    finally:
        resp.close()
        conn.close()


@pytest.mark.parametrize("path", ["/package.json", "/ccfeed/hub.js", "/tools/smoke.js"])
def test_feed_package_tools_and_package_json_are_never_served(start, path):
    base, _ = start()
    assert get(base + path)[0] == 404


def test_csp_allows_only_same_origin_scripts(serve):
    csp = serve.build_csp({})
    assert "script-src 'self';" in csp
    assert "jsdelivr" not in csp


def test_feed_settings_come_from_args_then_env(serve):
    s = serve.feed_settings({}, None)
    assert s == {"demo": False, "seed": 7, "interval": 2.0, "delta_path": str(DASH.parent / "data" / "delta_lake")}
    s = serve.feed_settings({"CC_DEMO": "1", "CC_DEMO_SEED": "3", "DELTA_LAKE_PATH": "/lake"}, None)
    assert s == {"demo": True, "seed": 3, "interval": 1.0, "delta_path": "/lake"}
    args = serve.build_parser().parse_args(["--demo", "--demo-seed", "9", "--interval", "0.5", "--delta-path", "/x"])
    assert serve.feed_settings({}, args) == {"demo": True, "seed": 9, "interval": 0.5, "delta_path": "/x"}
    with pytest.raises(SystemExit):
        serve.feed_settings({"CC_FEED_INTERVAL": "0"}, None)


def test_service_probe_reports_redis_and_metrics_honestly(serve):
    def broken_redis():
        raise ConnectionRefusedError("no redis")

    rows = serve.service_probe(broken_redis, ("q",), "http://127.0.0.1:9/metrics", 0.5)
    by_id = {r["id"]: r for r in rows}
    assert by_id["redis"]["status"] == "down" and "ConnectionRefusedError" in by_id["redis"]["detail"]
    assert by_id["metrics"]["status"] == "down"

    class FakeRedis:
        def ping(self):
            return True

        def type(self, key):
            return "list"

        def llen(self, key):
            return 7

    rows = serve.service_probe(lambda: FakeRedis(), ("q",), "ftp://nope", 0.5)
    redis_row = next(r for r in rows if r["id"] == "redis")
    assert redis_row["status"] == "ok"
    assert redis_row["data"]["queues"] == [{"key": "q", "type": "list", "depth": 7}]


def test_demo_mode_needs_no_pyarrow_or_deltalake(tmp_path):
    """The visual CI job installs only Playwright: --demo must run on the standard library."""
    import subprocess
    import sys

    code = f"""
import importlib.util, sys
for name in ("pyarrow", "deltalake", "redis"):
    sys.modules[name] = None  # any import of these now raises ImportError
spec = importlib.util.spec_from_file_location("s", {str(DASH / "serve.py")!r})
serve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(serve)
serve.DashboardHandler.feed_cfg = {{"demo": True, "seed": 1, "interval": 60.0, "delta_path": "/nope"}}
hub = serve.DashboardHandler.get_hub()
snap = hub.snapshot()
hub.stop()
print(snap["mode"], len(snap["stages"]))
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["demo", "5"]


def test_main_starts_the_feed_before_serving(serve, monkeypatch):
    """Backfill and the first poll happen at launch, not on the first visitor's request."""
    order = []

    class Stop(Exception):
        pass

    class FakeServer:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def serve_forever(self):
            order.append("serve")
            raise Stop

        def shutdown(self):
            pass

    monkeypatch.setattr(serve, "make_server", lambda host, port, **kw: FakeServer())
    monkeypatch.setattr(serve.DashboardHandler, "_hub", None)
    monkeypatch.setattr(serve.DashboardHandler, "hub_factory", staticmethod(lambda: order.append("hub") or object()))
    with pytest.raises(Stop):
        serve.main(["--demo", "--port", "8123"])
    assert order == ["hub", "serve"]
