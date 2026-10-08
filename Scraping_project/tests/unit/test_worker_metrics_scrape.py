"""#789: every pipeline worker exposes /metrics and every deployment path scrapes it."""

from __future__ import annotations

import ast
import shutil
import socket
import subprocess
import urllib.request
from pathlib import Path

import pytest
import yaml

from src.utils import worker_metrics as wm

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "k8s" / "helm" / "scraping-pipeline"
TEMPLATES = CHART / "templates"


@pytest.fixture(autouse=True)
def _fresh_server():
    wm._reset_for_tests()
    yield
    wm._reset_for_tests()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _scrape(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as resp:
        return resp.read().decode()


# --- runtime -----------------------------------------------------------------


@pytest.mark.parametrize(
    "env, expected",
    [
        ({}, 9430),
        ({"WORKER_METRICS_PORT": "9555"}, 9555),
        ({"WORKER_METRICS_PORT": " 9556 "}, 9556),
        ({"WORKER_METRICS_PORT": ""}, 9430),
        ({"WORKER_METRICS_PORT": "abc"}, 9430),
        ({"WORKER_METRICS_PORT": "70000"}, 9430),
        ({"WORKER_METRICS_PORT": "-1"}, 9430),
        ({"WORKER_METRICS_ENABLED": "0"}, None),
        ({"WORKER_METRICS_ENABLED": "false", "WORKER_METRICS_PORT": "9555"}, None),
        ({"WORKER_METRICS_ENABLED": "OFF"}, None),
        ({"WORKER_METRICS_ENABLED": "1"}, 9430),
    ],
)
def test_metrics_port_parsing(env, expected):
    assert wm.metrics_port(env) == expected


def test_default_port_is_outside_scrapy_range():
    assert not 9410 <= wm.DEFAULT_PORT <= 9420


def test_server_serves_worker_registry_and_is_idempotent():
    port = _free_port()
    env = {"WORKER_METRICS_PORT": str(port), "WORKER_METRICS_ADDR": "127.0.0.1"}
    assert wm.start_worker_metrics_server("stage2", env) == port
    body = _scrape(port)
    assert 'scrapy_worker_up{component="stage2"} 1.0' in body
    # process-wide registry: counters registered anywhere in the worker are exported
    assert "python_info" in body or "process_" in body
    # a second call (e.g. both shim and module entrypoint) reuses the server
    assert wm.start_worker_metrics_server("stage2", {"WORKER_METRICS_PORT": str(_free_port())}) == port


def test_port_in_use_does_not_crash_the_worker(caplog):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        busy = blocker.getsockname()[1]
        env = {"WORKER_METRICS_PORT": str(busy), "WORKER_METRICS_ADDR": "127.0.0.1"}
        assert wm.start_worker_metrics_server("stage3", env) is None
    assert "could not bind" in caplog.text


def test_disabled_starts_nothing():
    assert wm.start_worker_metrics_server("stage4", {"WORKER_METRICS_ENABLED": "no"}) is None
    assert wm._port is None


# --- entrypoints -------------------------------------------------------------


def _main_block_calls(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.If) and "__main__" in ast.unparse(node.test):
            return [
                ast.unparse(n.func)
                for n in ast.walk(node)
                if isinstance(n, ast.Call)
            ]
    return []


@pytest.mark.parametrize("n", [2, 3, 4])
def test_module_entrypoint_starts_metrics_before_loop(n):
    # Helm runs `python -u src/stageN/stageN_worker.py`
    calls = _main_block_calls(ROOT / "src" / f"stage{n}" / f"stage{n}_worker.py")
    assert "start_worker_metrics_server" in calls
    assert calls.index("start_worker_metrics_server") < calls.index("asyncio.run")


@pytest.mark.parametrize("n", [2, 3, 4])
def test_compose_shim_starts_metrics(n, monkeypatch):
    # docker-compose / plain manifest run `python -m src.workers.stageN_worker`
    import asyncio
    import importlib

    started: list[str] = []
    monkeypatch.setattr(wm, "start_worker_metrics_server", lambda c, env=None: started.append(c))
    monkeypatch.setattr(asyncio, "run", lambda coro: coro.close())
    importlib.import_module(f"src.workers.stage{n}_worker").main()
    assert started == [f"stage{n}"]


# --- Helm chart (static; CI has no helm binary) -------------------------------


def test_values_declare_worker_metrics():
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    assert values["workerMetrics"]["enabled"] is True
    assert values["workerMetrics"]["port"] == wm.DEFAULT_PORT
    assert values["workerMetrics"]["path"] == "/metrics"


def test_stage_deployments_annotated_and_expose_port():
    text = (TEMPLATES / "stage-workers-deployments.yaml").read_text()
    for key in ("prometheus.io/scrape", "prometheus.io/port", "prometheus.io/path"):
        assert text.count(key) == 3, key  # stage2 + stage3 + stage4 (#504)
    assert text.count("containerPort: {{ .Values.workerMetrics.port }}") == 3
    assert text.count("WORKER_METRICS_PORT") == 3


def test_prometheus_has_per_pod_jobs_for_workers():
    text = (TEMPLATES / "prometheus-statefulset.yaml").read_text()
    assert "job_name: '{{ $stage }}_worker'" in text
    assert "dns_sd_configs" in text
    services = (TEMPLATES / "worker-metrics-services.yaml").read_text()
    assert "clusterIP: None" in services  # headless -> one target per pod


def test_network_policy_admits_prometheus_on_targets():
    text = (TEMPLATES / "networkpolicy.yaml").read_text()
    assert "metrics-ingress" in text
    # untilStep needs int; `add` yields int64 and broke rendering entirely
    assert "{{- $metricsEnd := int (add" in text


def test_plain_manifest_worker_annotated():
    docs = [d for d in yaml.safe_load_all((ROOT / "k8s" / "deployment.yaml").read_text()) if d]
    stage2 = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "stage2-worker")
    pod = stage2["spec"]["template"]
    assert pod["metadata"]["annotations"]["prometheus.io/port"] == str(wm.DEFAULT_PORT)
    ports = [p["containerPort"] for c in pod["spec"]["containers"] for p in c.get("ports", [])]
    assert wm.DEFAULT_PORT in ports


def test_compose_prometheus_scrapes_every_worker():
    cfg = yaml.safe_load((ROOT / "monitoring" / "prometheus.yml").read_text())
    jobs = {j["job_name"]: j for j in cfg["scrape_configs"]}
    for n in (2, 3, 4):
        sd = jobs[f"stage{n}_worker"]["dns_sd_configs"][0]
        assert sd["names"] == [f"stage{n}-worker"]
        assert sd["port"] == wm.DEFAULT_PORT


# --- Helm render (only where helm is installed) --------------------------------

HELM = shutil.which("helm")


def _render(*sets: str) -> list[dict]:
    args = [HELM, "template", "t", str(CHART)]
    for s in sets:
        args += ["--set", s]
    out = subprocess.run(args, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


@pytest.mark.skipif(HELM is None, reason="helm not installed")
@pytest.mark.parametrize("np_enabled", ["true", "false"])
def test_rendered_chart_scrapes_every_stage(np_enabled):
    docs = _render(f"networkPolicy.enabled={np_enabled}")
    deps = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Deployment"}
    for stage in ("stage2", "stage3"):
        pod = deps[f"t-scraping-pipeline-{stage}"]["spec"]["template"]
        assert pod["metadata"]["annotations"]["prometheus.io/scrape"] == "true"
    prom = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("prometheus-config"))
    jobs = {j["job_name"] for j in yaml.safe_load(prom["data"]["prometheus.yml"])["scrape_configs"]}
    assert {"stage2_worker", "stage3_worker", "scrapy_app"} <= jobs
    if np_enabled == "true":
        ingress = {
            d["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/component"]
            for d in docs
            if d["kind"] == "NetworkPolicy" and d["metadata"]["name"].endswith("metrics-ingress")
        }
        assert {"scrapy", "metrics-exporter", "stage2", "stage3", "statsd-exporter"} <= ingress


@pytest.mark.skipif(HELM is None, reason="helm not installed")
def test_rendered_chart_without_worker_metrics():
    docs = _render("workerMetrics.enabled=false", "networkPolicy.enabled=true")
    prom = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("prometheus-config"))
    jobs = {j["job_name"] for j in yaml.safe_load(prom["data"]["prometheus.yml"])["scrape_configs"]}
    assert "stage2_worker" not in jobs
    assert not any(d["kind"] == "Service" and d["metadata"]["name"].endswith("stage2-metrics") for d in docs)
