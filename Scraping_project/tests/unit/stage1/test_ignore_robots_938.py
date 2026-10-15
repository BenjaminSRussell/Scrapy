"""rustmapper ignore_robots must be dual-confirmed before Stage 1 inherits it (#938)."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

from src.stage1.discovery_handoff import consume_rustmapper_handoff
from src.stage1.ignore_robots import (
    CONFIRM_PHRASE,
    apply_project_robotstxt_obey,
    as_bool,
    decide,
    decision_file_authorizes,
    ignore_robots_requested_in_jsonl,
    read_audit,
    write_decision_file,
)

pytestmark = pytest.mark.unit


def test_unrecognized_ignore_flag_stays_false():
    assert as_bool(None, default=False) is False
    assert as_bool("false") is False
    assert as_bool("0") is False
    assert as_bool("no") is False
    assert as_bool(0) is False
    assert as_bool("true") is True
    assert as_bool(1) is True
    assert as_bool("maybe") is False


def test_default_obeys_and_writes_audit(tmp_path):
    log = tmp_path / "audit.jsonl"
    decision = decide(audit_path_override=log, actor="ops")
    assert decision.robotstxt_obey is True
    assert decision.ignore_robots is False
    assert decision.outcome == "obey"
    assert decision.rustmapper_ignore_robots is False
    assert decision.stage1_confirm is False
    rows = read_audit(log)
    assert rows[-1]["action"] == "ignore_robots"
    assert rows[-1]["outcome"] == "obey"
    assert rows[-1]["robotstxt_obey"] is True


def test_rustmapper_true_alone_is_refused(tmp_path):
    log = tmp_path / "audit.jsonl"
    decision = decide(rustmapper_ignore_robots=True, audit_path_override=log)
    assert decision.outcome == "refused"
    assert decision.robotstxt_obey is True
    assert "not inherited" in decision.detail
    assert read_audit(log)[-1]["rustmapper_ignore_robots"] is True
    assert read_audit(log)[-1]["stage1_confirm"] is False


def test_stage1_confirm_alone_is_refused(tmp_path):
    decision = decide(
        stage1_confirm=True,
        typed=CONFIRM_PHRASE,
        reason="owned",
        audit_path_override=tmp_path / "audit.jsonl",
    )
    assert decision.outcome == "refused"
    assert decision.robotstxt_obey is True


def test_both_sides_need_reason_and_phrase(tmp_path):
    log = tmp_path / "audit.jsonl"
    missing_reason = decide(
        rustmapper_ignore_robots=True,
        stage1_confirm=True,
        typed=CONFIRM_PHRASE,
        audit_path_override=log,
    )
    wrong_phrase = decide(
        rustmapper_ignore_robots=True,
        stage1_confirm=True,
        typed="yes",
        reason="owned lab host",
        audit_path_override=log,
    )
    assert missing_reason.outcome == "refused"
    assert wrong_phrase.outcome == "refused"
    assert all(row["robotstxt_obey"] is True for row in read_audit(log))


def test_dual_confirm_authorizes_and_audits(tmp_path):
    log = tmp_path / "audit.jsonl"
    decision_path = tmp_path / "decision.json"
    decision = decide(
        rustmapper_ignore_robots="true",
        stage1_confirm="yes",
        typed=f"  {CONFIRM_PHRASE}  ",
        reason="owned lab host",
        source="cli",
        actor="ops",
        audit_path_override=log,
    )
    assert decision.outcome == "authorized"
    assert decision.ignore_robots is True
    assert decision.robotstxt_obey is False
    write_decision_file(decision_path, decision)
    assert decision_file_authorizes(path=decision_path) is True
    row = read_audit(log)[-1]
    assert row["outcome"] == "authorized"
    assert row["reason"] == "owned lab host"
    assert row["actor"] == "ops"
    assert row["source"] == "cli"


def test_yes_needs_allow_env(tmp_path, monkeypatch):
    log = tmp_path / "audit.jsonl"
    monkeypatch.delenv("ALLOW_IGNORE_ROBOTS", raising=False)
    refused = decide(
        rustmapper_ignore_robots=True,
        stage1_confirm=True,
        reason="owned",
        assume_yes=True,
        env={},
        audit_path_override=log,
    )
    assert refused.outcome == "refused"
    allowed = decide(
        rustmapper_ignore_robots=True,
        stage1_confirm=True,
        reason="owned",
        assume_yes=True,
        env={"ALLOW_IGNORE_ROBOTS": "1"},
        audit_path_override=log,
    )
    assert allowed.outcome == "authorized"
    assert allowed.robotstxt_obey is False


def test_explicit_opt_out_unchanged_until_rustmapper_requests_ignore(tmp_path):
    assert apply_project_robotstxt_obey(False, rustmapper_ignore_robots=None, config_ignore_robots=False) is False
    assert apply_project_robotstxt_obey(True, rustmapper_ignore_robots="false", config_ignore_robots=False) is True
    assert apply_project_robotstxt_obey(False, rustmapper_ignore_robots=True, env={}) is True
    decision_path = tmp_path / "decision.json"
    decision_path.write_text(
        json.dumps(
            {
                "outcome": "authorized",
                "rustmapper_ignore_robots": True,
                "stage1_confirm": True,
                "reason": "owned",
                "phrase_ok": True,
                "robotstxt_obey": False,
            }
        ),
        encoding="utf-8",
    )
    assert (
        apply_project_robotstxt_obey(
            True,
            rustmapper_ignore_robots=True,
            env={"IGNORE_ROBOTS_DECISION_FILE": str(decision_path)},
        )
        is False
    )
    incomplete = tmp_path / "partial.json"
    incomplete.write_text(json.dumps({"outcome": "authorized", "robotstxt_obey": False}), encoding="utf-8")
    assert decision_file_authorizes(path=incomplete) is False
    assert apply_project_robotstxt_obey(False, config_ignore_robots=True, env={"IGNORE_ROBOTS_DECISION_FILE": str(incomplete)}) is True


def test_jsonl_flag_is_requested_but_not_inherited(tmp_path):
    assert ignore_robots_requested_in_jsonl('{"url": "https://example.edu/a"}\n') is False
    jsonl = tmp_path / "sitemap.jsonl"
    jsonl.write_text(
        "\n".join(
            [
                json.dumps({"ignore_robots": True}),
                json.dumps({"url": "https://example.edu/a"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    handoff = consume_rustmapper_handoff(jsonl, site="example.edu")
    assert handoff.ignore_robots_requested is True
    assert handoff.discovered == ["https://example.edu/a"]
    posture = decide(
        rustmapper_ignore_robots=handoff.ignore_robots_requested,
        record=False,
    )
    assert posture.robotstxt_obey is True
    assert posture.outcome == "refused"


def test_string_false_on_sidecar_stays_obey(tmp_path):
    jsonl = tmp_path / "sitemap.jsonl"
    jsonl.write_text(json.dumps({"url": "https://example.edu/a"}) + "\n", encoding="utf-8")
    sidecar = tmp_path / "handoff.json"
    sidecar.write_text(json.dumps({"status": "success", "discovered": ["https://example.edu/a"], "ignore_robots": "false"}), encoding="utf-8")
    handoff = consume_rustmapper_handoff(jsonl, sidecar)
    assert handoff.ignore_robots_requested is False


def test_cli_status_and_confirm(monkeypatch, capsys, tmp_path):
    import cli

    log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("IGNORE_ROBOTS_AUDIT_LOG", str(log))
    monkeypatch.setattr(sys, "argv", ["cli.py", "robots", "status"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert "robotstxt_obey=true" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["cli.py", "robots", "confirm", "--rustmapper-ignore", "--reason", "owned"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 3
    assert "ROBOTSTXT_OBEY=true" in capsys.readouterr().out

    decision = tmp_path / "decision.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli.py",
            "robots",
            "confirm",
            "--rustmapper-ignore",
            "--confirm",
            "--reason",
            "owned lab host",
            "--typed",
            CONFIRM_PHRASE,
            "--decision-file",
            str(decision),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert decision_file_authorizes(path=decision) is True
    assert any(row["outcome"] == "authorized" for row in read_audit(log))


def test_settings_do_not_inherit_rustmapper_ignore(monkeypatch):
    import src.settings as settings

    monkeypatch.setenv("RUSTMAPPER_IGNORE_ROBOTS", "1")
    monkeypatch.delenv("IGNORE_ROBOTS_DECISION_FILE", raising=False)
    try:
        importlib.reload(settings)
        assert settings.ROBOTSTXT_OBEY is True
    finally:
        monkeypatch.delenv("RUSTMAPPER_IGNORE_ROBOTS", raising=False)
        importlib.reload(settings)
        assert settings.ROBOTSTXT_OBEY is True


def test_ui_endpoint_audits_refusal_and_confirm(monkeypatch, tmp_path):
    import importlib.util
    import threading
    import urllib.request

    dash = Path(__file__).resolve().parents[3] / "dashboard"
    spec = importlib.util.spec_from_file_location("_cc_robots_938", dash / "serve.py")
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("IGNORE_ROBOTS_AUDIT_LOG", str(log))
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html><title>CC</title>")
    env = {"IGNORE_ROBOTS_AUDIT_LOG": str(log)}
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
            "metrics_upstream": serve.DEFAULT_METRICS_UPSTREAM,
            "upstream_timeout": 4.0,
            "queue_keys": serve.queue_keys(env),
        },
    )
    httpd = serve.make_server("127.0.0.1", 0, handler=handler, env=env)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        refused = urllib.request.Request(
            base + "/api/stage1/ignore-robots",
            data=json.dumps({"rustmapper_ignore_robots": True}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(refused, timeout=5) as response:
            body = json.loads(response.read().decode())
        assert body["outcome"] == "refused"
        assert body["robotstxt_obey"] is True

        ok = urllib.request.Request(
            base + "/api/stage1/ignore-robots",
            data=json.dumps(
                {
                    "rustmapper_ignore_robots": True,
                    "stage1_confirm": True,
                    "reason": "owned lab host",
                    "typed": CONFIRM_PHRASE,
                    "actor": "ui-ops",
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(ok, timeout=5) as response:
            body = json.loads(response.read().decode())
        assert body["outcome"] == "authorized"
        assert body["robotstxt_obey"] is False
        assert body["source"] == "ui"
        rows = read_audit(log)
        assert [row["outcome"] for row in rows] == ["refused", "authorized"]
        assert rows[-1]["actor"] == "ui-ops"
    finally:
        httpd.shutdown()
        httpd.server_close()
