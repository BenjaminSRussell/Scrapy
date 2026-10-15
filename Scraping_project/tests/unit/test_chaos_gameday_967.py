"""Chaos game-day stubs (#967). Staging only, env-guarded, no Redis or Kafka.

Productizes the safe trigger for the chaos scenarios in #250. The buttons
record a request. They do not stop a process.
"""
from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

DASH = Path(__file__).resolve().parents[2] / "dashboard"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, DASH / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def serve():
    return _load("_cc_serve_chaos_967", "serve.py")


@pytest.fixture(autouse=True)
def _clear_journal(serve):
    serve.chaos_gameday.reset_journal()
    yield
    serve.chaos_gameday.reset_journal()


def test_module_does_not_reach_redis_kafka_or_processes(serve):
    text = (DASH / "chaos_gameday.py").read_text(encoding="utf-8")
    for banned in ("import redis", "import kafka", "subprocess", "os.kill", "REDIS_URL", "docker"):
        assert banned not in text
    assert serve.chaos_gameday.POSTMORTEM == "chaos-gameday-postmortem.txt"
    template = (DASH / serve.chaos_gameday.POSTMORTEM).read_text(encoding="utf-8")
    assert "Chaos game-day postmortem" in template
    assert "kill-redis" in template and "pause-kafka" in template


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"CC_ENV": "staging"},
        {"CC_CHAOS_GAMEDAY": "1"},
        {"CC_ENV": "staging", "CC_CHAOS_GAMEDAY": "yesplease"},
        {"CC_ENV": "production", "CC_CHAOS_GAMEDAY": "1"},
        {"CC_ENV": "prod", "CC_CHAOS_GAMEDAY": "on"},
        {"CC_ENV": "staging-prod", "CC_CHAOS_GAMEDAY": "1"},
        {"ENV": "production", "CC_ENV": "staging", "CC_CHAOS_GAMEDAY": "true"},
        {"APP_ENV": "prod", "CC_ENV": "staging", "CC_CHAOS_GAMEDAY": "yes"},
        {"CC_ENV": "local", "CC_CHAOS_GAMEDAY": "1"},
    ],
)
def test_disarmed_environments_refuse_both_stubs(serve, env):
    body = serve.chaos_gameday.status(env)
    assert body["enabled"] is False
    assert body["actions"] == []
    assert body["stub"] is True and body["effect"] == "none"
    assert body["postmortem"].endswith("chaos-gameday-postmortem.txt")
    for action in ("kill-redis", "pause-kafka"):
        status, result = serve.chaos_gameday.inject(env, action)
        assert status == 403
        assert result["ok"] is False and result["effect"] == "none"
    assert serve.chaos_gameday.journal() == []


@pytest.mark.parametrize(
    "env",
    [
        {"CC_ENV": "staging", "CC_CHAOS_GAMEDAY": "1"},
        {"CC_ENV": "Staging", "CC_CHAOS_GAMEDAY": "true"},
        {"ENV": "staging", "CC_CHAOS_GAMEDAY": "yes"},
        {"APP_ENV": "staging", "CC_CHAOS_GAMEDAY": "on"},
    ],
)
def test_staging_flag_arms_stubs_without_an_effect(serve, env):
    body = serve.chaos_gameday.status(env)
    assert body["enabled"] is True
    assert body["env"] == "staging"
    assert body["actions"] == ["kill-redis", "pause-kafka"]
    status, result = serve.chaos_gameday.inject(env, "kill-redis")
    assert status == 200
    assert result == {
        "ok": True,
        "accepted": "kill-redis",
        "stub": True,
        "effect": "none",
        "recorded": True,
        "postmortem": "chaos-gameday-postmortem.txt",
    }
    status, result = serve.chaos_gameday.inject(env, "pause-kafka")
    assert status == 200 and result["accepted"] == "pause-kafka" and result["effect"] == "none"
    assert [row["action"] for row in serve.chaos_gameday.journal()] == ["kill-redis", "pause-kafka"]


def test_unknown_action_is_rejected_and_not_recorded(serve):
    env = {"CC_ENV": "staging", "CC_CHAOS_GAMEDAY": "1"}
    status, result = serve.chaos_gameday.inject(env, "drop-database")
    assert status == 400
    assert result["ok"] is False and result["effect"] == "none"
    assert serve.chaos_gameday.journal() == []


def test_journal_stays_bounded(serve):
    env = {"CC_ENV": "staging", "CC_CHAOS_GAMEDAY": "1"}
    for _ in range(60):
        assert serve.chaos_gameday.inject(env, "pause-kafka")[0] == 200
    assert len(serve.chaos_gameday.journal()) == 50


def _handler(serve, root, env):
    calls = []

    def redis_factory():
        calls.append("redis")
        raise AssertionError("chaos game day must not open Redis")

    handler = type(
        "H",
        (serve.DashboardHandler,),
        {
            "root": root,
            "log_message": lambda *a: None,
            "auth": serve.AuthGate.from_env(env),
            "limiter": serve.RateLimiter.from_env(env),
            "cors_origins": serve.parse_cors_origins(env),
            "csp": serve.build_csp(env),
            "metrics_upstream": "http://127.0.0.1:9/metrics",
            "upstream_timeout": 0.2,
            "queue_keys": (),
            "chaos_env": dict(env),
            "redis_factory": staticmethod(redis_factory),
        },
    )
    handler.redis_calls = calls
    return handler


@pytest.fixture
def chaos_server(serve, tmp_path):
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html><title>CC</title>")
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


def _request(url, payload=None, method="GET"):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        return exc.code, json.loads(raw) if raw else {}


def test_http_stubs_are_off_until_staging_is_armed(chaos_server, serve):
    base, handler = chaos_server({})
    status, body = _request(base + "/api/chaos/gameday")
    assert status == 200 and body["enabled"] is False and body["actions"] == []
    status, body = _request(base + "/api/chaos/gameday", {"action": "kill-redis"}, method="POST")
    assert status == 403 and body["effect"] == "none"
    assert handler.redis_calls == []

    armed, armed_handler = chaos_server({"CC_ENV": "staging", "CC_CHAOS_GAMEDAY": "1"})
    status, body = _request(armed + "/api/chaos/gameday")
    assert status == 200 and body["actions"] == ["kill-redis", "pause-kafka"]
    status, body = _request(armed + "/api/chaos/gameday", {"action": "pause-kafka"}, method="POST")
    assert status == 200 and body["accepted"] == "pause-kafka" and body["effect"] == "none"
    status, body = _request(armed + "/api/chaos/gameday", {"action": "kill-redis"}, method="POST")
    assert status == 200 and body["stub"] is True
    status, body = _request(armed + "/api/chaos/gameday", {"action": "wipe"}, method="POST")
    assert status == 400 and body["effect"] == "none"
    assert armed_handler.redis_calls == []
    assert [row["action"] for row in serve.chaos_gameday.journal()] == ["pause-kafka", "kill-redis"]
