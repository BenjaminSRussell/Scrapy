"""#404: first-run wizard API (dashboard/serve.py) and the one-time start.py hint."""
from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DASH = ROOT / "dashboard"


@pytest.fixture(scope="module")
def serve():
    spec = importlib.util.spec_from_file_location("_cc_serve_404", DASH / "serve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wizard(serve, tmp_path, monkeypatch):
    """Serve the dashboard with first-run paths redirected into tmp_path and every probe pointed at a closed port."""
    for var in ("POSTGRES_HOST", "POSTGRES_PORT", "PROMETHEUS_HEALTH_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("POSTGRES_PORT", "1")
    monkeypatch.setenv("PROMETHEUS_HEALTH_URL", "http://127.0.0.1:1/-/healthy")
    seeds = tmp_path / "uconn_urls.csv"
    seeds.write_text("\n".join(f"https://seed{i}.example/" for i in range(8)) + "\nnot-a-url\n")
    cli = tmp_path / "cli.py"
    # Stub for cli.py: logs its argv and fails the seed4 row so the partial-failure path runs.
    cli.write_text(
        "import sys\n"
        "with open(sys.argv[0] + '.log', 'a') as f:\n"
        "    f.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "sys.exit(3 if sys.argv[3] == 'https://seed4.example/' else 0)\n"
    )

    def no_redis():
        raise ImportError("no redis")

    handler = type("H", (serve.DashboardHandler,), {
        "root": tmp_path,
        "log_message": lambda *a: None,
        "auth": serve.AuthGate.from_env({}),
        "limiter": serve.RateLimiter(per_minute=0),
        "cors_origins": (),
        "csp": serve.build_csp({}),
        "metrics_upstream": "http://127.0.0.1:1/metrics",
        "upstream_timeout": 1.0,
        "queue_keys": (),
        "grafana_url": "http://127.0.0.1:1/",
        "redis_factory": staticmethod(no_redis),
        "first_run_flag": tmp_path / "config" / "first_run.json",
        "seed_file": seeds,
        "cli_path": cli,
    })
    httpd = serve.make_server("127.0.0.1", 0, handler=handler, env={})
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", handler
    httpd.shutdown()
    httpd.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, json.loads(resp.read())


def _post(url, body=b"{}", content_type="application/json"):
    headers = {"Content-Type": content_type} if content_type else {}
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:  # send_error() answers 404 with HTML, not JSON
            return e.code, {"raw": raw.decode("utf-8", "replace")}


def test_status_lists_all_five_services_and_not_completed(wizard):
    base, _ = wizard
    status, payload = _get(f"{base}/api/first-run")
    assert status == 200
    assert payload["completed"] is False
    assert [c["name"] for c in payload["checks"]] == ["redis", "postgres", "delta", "grafana", "prometheus"]
    by_name = {c["name"]: c for c in payload["checks"]}
    assert by_name["redis"]["ok"] is False
    assert by_name["postgres"]["ok"] is False
    assert by_name["grafana"]["ok"] is False
    assert by_name["prometheus"]["ok"] is False


def test_complete_writes_flag_once_and_status_reflects_it(wizard):
    base, handler = wizard
    assert _post(f"{base}/api/first-run/complete")[0] == 200
    assert handler.first_run_flag.exists()
    assert "completed_at" in json.loads(handler.first_run_flag.read_text())
    assert _get(f"{base}/api/first-run")[1]["completed"] is True


def test_post_requires_json_content_type(wizard):
    base, handler = wizard
    status, body = _post(f"{base}/api/first-run/complete", content_type="text/plain")
    assert status == 415
    assert body["ok"] is False
    assert not handler.first_run_flag.exists()
    assert _post(f"{base}/api/first-run/sample-seeds", content_type=None)[0] == 415


def test_sample_seeds_use_cli_seeds_add_for_first_valid_urls(wizard):
    base, handler = wizard
    status, body = _post(f"{base}/api/first-run/sample-seeds")
    assert body["added"] == 4
    assert status == 207
    assert body["ok"] is False
    assert [f["url"] for f in body["failed"]] == ["https://seed4.example/"]
    log = Path(str(handler.cli_path) + ".log").read_text().splitlines()
    assert len(log) == 5
    assert log[0] == "seeds add https://seed0.example/ --note first-run sample --actor first-run"
    assert all(line.startswith("seeds add https://") for line in log)


def test_sample_seeds_all_ok_returns_200(wizard):
    base, handler = wizard
    handler.cli_path.write_text("import sys\nsys.exit(0)\n")
    status, body = _post(f"{base}/api/first-run/sample-seeds")
    assert status == 200
    assert body == {"ok": True, "added": 5, "failed": []}


def test_unknown_post_route_is_404(wizard):
    base, _ = wizard
    assert _post(f"{base}/api/nope")[0] == 404


def test_start_hint_prints_until_flag_exists(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location("_ops_start_404", ROOT / "start.py")
    start = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(start)

    flag = tmp_path / "first_run.json"
    start.print_first_run_hint(flag)
    assert "First run: open the Control Center at http://localhost:8080" in capsys.readouterr().out

    flag.write_text("{}")
    start.print_first_run_hint(flag)
    assert capsys.readouterr().out == ""


def test_start_flag_path_matches_dashboard(serve):
    spec = importlib.util.spec_from_file_location("_ops_start_404b", ROOT / "start.py")
    start = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(start)
    assert start.FIRST_RUN_FLAG == serve.FIRST_RUN_FLAG
