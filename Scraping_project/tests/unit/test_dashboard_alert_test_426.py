"""#426: the Control Center test-notification route. A local receiver stands in for the webhook."""
from __future__ import annotations

import http.server
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
    spec = importlib.util.spec_from_file_location("_cc_serve_426", DASH / "serve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Receiver:
    """Records POST bodies. ``status`` is the code it answers with."""

    def __init__(self, status: int = 200):
        self.status = status
        self.bodies: list[dict] = []
        receiver = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                receiver.bodies.append(json.loads(self.rfile.read(length)))
                self.send_response(receiver.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/hook"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def receiver():
    r = _Receiver()
    yield r
    r.close()


@pytest.fixture
def make_server(serve, tmp_path):
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html><title>CC</title>")
    servers = []

    def start(env):
        handler = type(
            "H",
            (serve.DashboardHandler,),
            {
                "root": root,
                "log_message": lambda *a: None,
                "auth": serve.AuthGate.from_env(env),
                "limiter": serve.RateLimiter.from_env(env),
                "cors_origins": (),
                "csp": serve.build_csp(env),
                "metrics_upstream": serve.DEFAULT_METRICS_UPSTREAM,
                "upstream_timeout": 2.0,
                "queue_keys": (),
                "grafana_url": serve.grafana_base_url(env),
                "alert_webhook": serve.alert_webhook_url(env),
            },
        )
        httpd = serve.make_server("127.0.0.1", 0, handler=handler, env=env)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def post(url, data=b"{}", content_type="application/json"):
    headers = {"Content-Type": content_type} if content_type else {}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_webhook_url_must_be_plain_http_or_https(serve):
    assert serve.alert_webhook_url({"ALERT_WEBHOOK_URL": "https://hooks.example.test/x"}) == "https://hooks.example.test/x"
    assert serve.alert_webhook_url({}) is None
    assert serve.alert_webhook_url({"ALERT_WEBHOOK_URL": "file:///etc/passwd"}) is None
    assert serve.alert_webhook_url({"ALERT_WEBHOOK_URL": "https://u:p@hooks.example.test/x"}) is None
    assert serve.alert_webhook_url({"ALERT_WEBHOOK_URL": "https://hooks.example.test/x y"}) is None


def test_test_button_sends_the_payload_to_the_webhook(make_server, receiver):
    base = make_server({"ALERT_WEBHOOK_URL": receiver.url})
    status, body = post(base + "/api/alerts/test")
    assert status == 200
    assert body == {"ok": True, "status": 200}
    assert len(receiver.bodies) == 1
    assert receiver.bodies[0]["event"] == "job.test"
    assert receiver.bodies[0]["job"] == "test-notification"


def test_webhook_error_status_is_reported_as_502(make_server, serve):
    recv = _Receiver(status=500)
    try:
        base = make_server({"ALERT_WEBHOOK_URL": recv.url})
        status, body = post(base + "/api/alerts/test")
        assert status == 502
        assert body["ok"] is False
    finally:
        recv.close()


def test_no_webhook_configured_is_a_clear_409(make_server):
    base = make_server({})
    status, body = post(base + "/api/alerts/test")
    assert status == 409
    assert "ALERT_WEBHOOK_URL" in body["error"]


def test_plain_form_post_is_refused_before_any_send(make_server, receiver):
    base = make_server({"ALERT_WEBHOOK_URL": receiver.url})
    status, _ = post(base + "/api/alerts/test", content_type="application/x-www-form-urlencoded")
    assert status == 415
    assert receiver.bodies == []


def test_unknown_post_route_is_404(make_server):
    base = make_server({})
    status, _ = post(base + "/api/nope")
    assert status == 404
