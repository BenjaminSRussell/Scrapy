"""Dashboard POST /api/control writes the audit log; GET /api/audit lists it (#657)."""
from __future__ import annotations

import base64
import importlib.util
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from src.control.operator_audit import OperatorAuditLog

DASH = Path(__file__).resolve().parents[2] / "dashboard"


@pytest.fixture(scope="module")
def serve():
    spec = importlib.util.spec_from_file_location("_cc_serve_audit_657", DASH / "serve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _handler(serve, root, env, log, delta_path=None):
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
            "audit_log": log,
            "audit_delta_path": delta_path,
        },
    )


@pytest.fixture
def make_server(serve, tmp_path):
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html><title>CC</title>")
    log = OperatorAuditLog(tmp_path / "events.jsonl")
    servers = []

    def start(env, delta_path=None):
        handler = _handler(serve, root, env, log, delta_path)
        httpd = serve.make_server("127.0.0.1", 0, handler=handler, env=env)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}", log

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def _exchange(url, data=None, headers=None, method="GET"):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()


def post(url, payload, headers=None):
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    return _exchange(url, data=json.dumps(payload).encode(), headers=hdrs, method="POST")


def test_control_post_appends_and_audit_lists_newest(make_server):
    base, log = make_server({})
    status, _, body = post(base + "/api/control", {"action": "pause_stage2", "job_id": "job-1", "actor": "ada"})
    assert status == 201
    event = json.loads(body)["event"]
    assert event["actor"] == "ada"
    assert event["action"] == "pause_stage2"
    assert event["job_id"] == "job-1"
    assert event["ts"].endswith("Z")
    assert "delta_ok" not in json.loads(body)

    status, _, body = post(base + "/api/control", {"action": "resume_stage2", "job_id": "job-1", "actor": "ada"})
    assert status == 201
    status, _, body = _exchange(base + "/api/audit?limit=1")
    payload = json.loads(body)
    assert status == 200 and payload["ok"] is True and payload["limit"] == 1
    assert payload["events"][0]["action"] == "resume_stage2"
    raw = log.path.read_bytes()
    assert raw.count(b"\n") == 2
    assert b"pause_stage2" in raw and b"resume_stage2" in raw


def test_unknown_action_writes_nothing(make_server):
    base, log = make_server({})
    assert not log.path.exists()
    status, _, body = post(base + "/api/control", {"action": "drop_database", "actor": "ada"})
    assert status == 400
    assert b"unknown action" in body
    assert not log.path.exists()


def test_basic_auth_actor_is_the_user_and_anonymous_writes_nothing(make_server, tmp_path):
    base, log = make_server({"CC_BASIC_AUTH": "ops:s3cret"})
    marker = log.path.read_bytes() if log.path.exists() else b""
    status, _, _ = post(base + "/api/control", {"action": "pause", "actor": "eve"})
    assert status == 401
    assert (log.path.read_bytes() if log.path.exists() else b"") == marker

    good = "Basic " + base64.b64encode(b"ops:s3cret").decode()
    status, _, body = post(
        base + "/api/control",
        {"action": "pause", "actor": "eve", "job_id": "stage2"},
        headers={"Authorization": good},
    )
    assert status == 201
    assert json.loads(body)["event"]["actor"] == "ops"
    assert json.loads(body)["event"]["job_id"] == "stage2"


def test_proxy_actor_header_beats_json_actor(make_server):
    base, _log = make_server({})
    status, _, body = post(
        base + "/api/control",
        {"action": "stop", "actor": "eve"},
        headers={"X-Control-Actor": "bob"},
    )
    assert status == 201
    assert json.loads(body)["event"]["actor"] == "bob"


def test_get_control_is_405_and_audit_has_no_cors(make_server):
    base, _log = make_server({"CC_CORS_ORIGINS": "*"})
    status, headers, _ = _exchange(base + "/api/control")
    assert status == 405
    assert headers.get("allow") == "POST"
    status, headers, body = _exchange(base + "/api/audit", headers={"Origin": "http://evil.example"})
    assert status == 200
    assert "access-control-allow-origin" not in headers
    assert json.loads(body)["ok"] is True
    status, headers, _ = _exchange(base + "/", headers={"Origin": "http://evil.example"})
    assert status == 200
    assert headers.get("access-control-allow-origin") == "*"


def test_bad_limit(make_server):
    base, _log = make_server({})
    status, _, body = _exchange(base + "/api/audit?limit=0")
    assert status == 400
    assert b"limit" in body
    status, _, body = _exchange(base + "/api/audit?limit=nope")
    assert status == 400


def test_control_mirrors_delta_when_configured(make_server, tmp_path):
    pytest.importorskip("deltalake")
    table = tmp_path / "audit_delta"
    base, _log = make_server({}, delta_path=str(table))
    status, _, body = post(base + "/api/control", {"action": "start_stage1", "job_id": "j", "actor": "ada"})
    assert status == 201
    assert json.loads(body)["delta_ok"] is True
    from deltalake import DeltaTable

    rows = DeltaTable(str(table)).to_pyarrow_table().to_pylist()
    assert rows[0]["action"] == "start_stage1"
    assert rows[0]["actor"] == "ada"
