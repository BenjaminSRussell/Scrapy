"""Dashboard serve.py: auth (#189/#455), CSP/headers (#245), rate limit (#257),
metrics proxy (#400) and Redis queue depths (#401)."""
from __future__ import annotations

import base64
import importlib.util
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

DASH = Path(__file__).resolve().parents[2] / "dashboard"


@pytest.fixture(scope="module")
def serve():
    spec = importlib.util.spec_from_file_location("_cc_serve_sec", DASH / "serve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
        },
    )


@pytest.fixture
def make_server(serve, tmp_path):
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html><title>CC</title>")
    (root / "app.js").write_text("console.log('ok');")

    servers = []

    def start(env):
        handler = _handler(serve, root, env)
        httpd = serve.make_server("127.0.0.1", 0, handler=handler, env=env)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}", handler

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def get(url, headers=None, method="GET"):
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


# ---- CSP / security headers (#245) ----
def test_enforcing_csp_and_security_headers(make_server):
    base, _ = make_server({})
    status, headers, _ = get(base + "/")
    assert status == 200
    csp = headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "cdn.jsdelivr.net" in csp
    assert "Report-Only" not in headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert "camera=()" in headers["permissions-policy"]
    assert "access-control-allow-origin" not in headers  # default: no CORS


def test_cors_is_opt_in_and_wildcard_warns(make_server, serve, capsys):
    base, _ = make_server({"CC_CORS_ORIGINS": "http://ops.example"})
    status, headers, _ = get(base + "/", headers={"Origin": "http://ops.example"})
    assert status == 200 and headers["access-control-allow-origin"] == "http://ops.example"
    status, headers, _ = get(base + "/", headers={"Origin": "http://evil.example"})
    assert status == 200 and "access-control-allow-origin" not in headers
    make_server({"CC_CORS_ORIGINS": "*"})  # construction prints the warning
    assert "CC_CORS_ORIGINS=*" in capsys.readouterr().err


# ---- Auth (#189/#455) ----
def test_auth_disabled_by_default(make_server):
    base, _ = make_server({})
    assert get(base + "/")[0] == 200


def test_basic_auth_gate(make_server):
    base, _ = make_server({"CC_BASIC_AUTH": "ops:s3cret"})
    assert get(base + "/")[0] == 401
    good = "Basic " + base64.b64encode(b"ops:s3cret").decode()
    bad = "Basic " + base64.b64encode(b"ops:wrong").decode()
    assert get(base + "/", headers={"Authorization": good})[0] == 200
    assert get(base + "/", headers={"Authorization": bad})[0] == 401


def test_bearer_token_gate(make_server):
    base, _ = make_server({"CC_AUTH_TOKEN": "tok-xyz"})
    assert get(base + "/")[0] == 401
    assert get(base + "/", headers={"Authorization": "Bearer tok-xyz"})[0] == 200
    # token as Basic password with any user also works
    basic = "Basic " + base64.b64encode(b"anyone:tok-xyz").decode()
    assert get(base + "/", headers={"Authorization": basic})[0] == 200
    assert get(base + "/", headers={"Authorization": "Bearer wrong"})[0] == 401


def test_wildcard_bind_warns_when_auth_off(serve, capsys):
    serve.make_server("0.0.0.0", 1, server_factory=lambda a, h: object(), env={})
    err = capsys.readouterr().err
    assert "all interfaces" in err and "CC_AUTH_TOKEN" in err


# ---- Rate limit (#257) ----
def test_rate_limit_returns_429(make_server):
    base, _ = make_server({"CC_RATE_LIMIT": "60", "CC_RATE_BURST": "2"})  # 1/s, burst 2
    assert get(base + "/")[0] == 200
    assert get(base + "/")[0] == 200
    status, headers, body = get(base + "/")
    assert status == 429
    assert int(headers["retry-after"]) >= 1
    assert b"Too Many Requests" in body


def test_rate_limit_disabled_with_zero(make_server):
    base, _ = make_server({"CC_RATE_LIMIT": "0", "CC_RATE_BURST": "1"})
    for _ in range(5):
        assert get(base + "/")[0] == 200


# ---- Metrics proxy (#400) ----
def test_api_metrics_proxies_upstream(make_server, serve):
    calls = []

    def opener(req, timeout=4.0):  # noqa: ARG001
        calls.append(req.full_url)

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
        base, _ = make_server({"METRICS_UPSTREAM": "http://exporter:9090/metrics"})
        status, headers, body = get(base + "/api/metrics")
        assert status == 200 and body == b"pipeline_running 1\n"
        assert "text/plain" in headers["content-type"]
        assert calls == ["http://exporter:9090/metrics"]
        status, _, body = get(base + "/api/health")
        assert status == 200 and json.loads(body)["metrics_upstream"]["ok"] is True
    finally:
        serve.fetch_upstream = orig


def test_api_metrics_upstream_down(make_server, serve):
    def opener(req, timeout=4.0):  # noqa: ARG001
        raise urllib.error.URLError("down")

    orig = serve.fetch_upstream
    serve.fetch_upstream = lambda url, timeout=4.0, opener=opener: orig(url, timeout, opener)
    try:
        base, _ = make_server({"METRICS_UPSTREAM": "http://127.0.0.1:1/metrics"})
        status, _, body = get(base + "/api/metrics")
        assert status == 502 and json.loads(body)["ok"] is False
    finally:
        serve.fetch_upstream = orig


# ---- Queue depths (#401) ----
class FakeRedis:
    def __init__(self, data):
        self.data = data  # key -> (type, depth)

    def ping(self):
        return True

    def type(self, key):
        return self.data.get(key, ("none", 0))[0]

    def llen(self, key):
        return self.data[key][1]

    def zcard(self, key):
        return self.data[key][1]

    def scard(self, key):
        return self.data[key][1]

    def xlen(self, key):
        return self.data[key][1]

    def hlen(self, key):
        return self.data[key][1]


def test_queue_depths_helper_and_endpoint(serve, tmp_path, make_server):
    client = FakeRedis({"js_spider:priority_queue": ("zset", 7), "q2": ("list", 0)})
    assert serve.queue_depths(client, ("js_spider:priority_queue", "q2", "gone")) == [
        {"key": "js_spider:priority_queue", "type": "zset", "depth": 7},
        {"key": "q2", "type": "list", "depth": 0},
        {"key": "gone", "type": "none", "depth": 0},
    ]

    env = {"CC_QUEUE_KEYS": "js_spider:priority_queue,q2"}
    root = tmp_path / "s2"
    root.mkdir()
    (root / "index.html").write_text("x")
    handler = _handler(serve, root, env)
    handler.redis_factory = staticmethod(lambda: client)
    httpd = serve.make_server("127.0.0.1", 0, handler=handler, env=env)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        status, _, body = get(base + "/api/queues")
        assert status == 200
        payload = json.loads(body)
        assert payload["ok"] is True
        assert payload["queues"][0]["depth"] == 7
        assert payload["queues"][1]["depth"] == 0
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_api_queues_redis_down_is_503(serve, tmp_path):
    class Boom:
        def ping(self):
            raise ConnectionError("refused")

    env = {}
    root = tmp_path / "s3"
    root.mkdir()
    (root / "index.html").write_text("x")
    handler = _handler(serve, root, env)
    handler.redis_factory = staticmethod(lambda: Boom())
    httpd = serve.make_server("127.0.0.1", 0, handler=handler, env=env)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        status, _, body = get(base + "/api/queues")
        assert status == 503
        payload = json.loads(body)
        assert payload["ok"] is False and "redis unavailable" in payload["error"]
        # never invents zero depths
        assert "queues" not in payload
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_build_csp_extra_connect_src(serve):
    csp = serve.build_csp({"CC_CONNECT_SRC": "https://metrics.example, http://evil';\"drop"})
    assert "https://metrics.example" in csp
    assert "evil" not in csp  # rejected: quote / semicolon


def test_features_js_exists_and_index_has_no_inline_script():
    html = (DASH / "index.html").read_text()
    assert 'src="features.js"' in html
    assert "<script>" not in html.replace("<script ", "<X ")  # no bare <script> blocks
    assert (DASH / "features.js").is_file()
    assert "integrity=" in html and "chart.js@4.4.0" in html


def test_auth_gate_rejects_malformed_basic_env(serve):
    with pytest.raises(SystemExit):
        serve.AuthGate.from_env({"CC_BASIC_AUTH": "nocolon"})


# ---- Smoke test of the real dashboard docroot (#271) ----
def test_real_dashboard_assets_smoke(serve):
    """GET / and every local script index.html references, with the right Content-Type."""
    import re

    handler = _handler(serve, DASH, {"CC_RATE_LIMIT": "0"})
    httpd = serve.make_server("127.0.0.1", 0, handler=handler, env={})
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        status, headers, body = get(base + "/")
        assert status == 200 and headers["content-type"].startswith("text/html")
        html = body.decode()
        assert "<title>" in html
        local_scripts = [s for s in re.findall(r'<script src="([^"]+)"', html) if "://" not in s]
        assert {"features.js", "format-utils.js", "version.js", "app.js"} <= set(local_scripts)
        for src in local_scripts:
            status, headers, body = get(f"{base}/{src}")
            assert status == 200, src
            assert "javascript" in headers["content-type"], (src, headers["content-type"])
            assert body.strip(), src
        status, headers, _ = get(base + "/styleguide.html")
        assert status == 200 and headers["content-type"].startswith("text/html")
    finally:
        httpd.shutdown()
        httpd.server_close()
