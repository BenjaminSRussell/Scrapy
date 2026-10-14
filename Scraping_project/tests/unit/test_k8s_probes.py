"""#181: worker probes are stdlib-only, present on every plain-manifest Deployment."""

import socket
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pytest
import yaml

from src.utils import probe

ROOT = Path(__file__).resolve().parents[2]
linux_only = pytest.mark.skipif(not Path("/proc/self/cmdline").exists(), reason="needs /proc")


@linux_only
def test_alive_finds_other_process_but_never_itself():
    marker = f"probe-marker-{uuid.uuid4().hex}"
    assert probe.process_alive(marker) is False  # our own argv/test source don't count
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", marker])
    try:
        assert probe.process_alive(marker) is True
    finally:
        child.kill()
        child.wait()
    assert probe.process_alive(marker) is False


@linux_only
def test_alive_via_sh_c_does_not_match_its_own_shell():
    marker = f"probe-marker-{uuid.uuid4().hex}"
    code = f"/bin/sh -c '{sys.executable} -m src.utils.probe alive {marker}'"
    rc = subprocess.run(code, shell=True, cwd=ROOT).returncode
    assert rc == 1


def _listener():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    return srv


def test_tcp_ready():
    srv = _listener()
    port = srv.getsockname()[1]
    try:
        assert probe.tcp_ready("127.0.0.1", port) is True
    finally:
        srv.close()
    assert probe.tcp_ready("127.0.0.1", port, timeout=0.5) is False


@pytest.mark.parametrize("reply,ok", [(b"+PONG\r\n", True), (b"-NOAUTH Authentication required.\r\n", True),
                                      (b"HTTP/1.1 400\r\n", False)])
def test_redis_ready(monkeypatch, reply, ok):
    srv = _listener()

    def serve():
        conn, _ = srv.accept()
        conn.recv(64)
        conn.sendall(reply)
        conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    monkeypatch.setenv("REDIS_HOST", "127.0.0.1")
    monkeypatch.setenv("REDIS_PORT", str(srv.getsockname()[1]))
    try:
        assert probe.redis_ready(timeout=2) is ok
    finally:
        t.join(2)
        srv.close()


def test_cli_usage_error():
    assert probe.main(["bogus"]) == 2


def test_plain_manifest_deployments_all_have_probes():
    docs = [d for d in yaml.safe_load_all((ROOT / "k8s" / "deployment.yaml").read_text()) if d]
    deployments = [d for d in docs if d["kind"] == "Deployment"]
    assert {d["metadata"]["name"] for d in deployments} >= {"stage1-worker", "stage2-worker", "redis"}
    for d in deployments:
        for c in d["spec"]["template"]["spec"]["containers"]:
            assert "livenessProbe" in c and "readinessProbe" in c, (d["metadata"]["name"], c["name"])


def test_no_pgrep_probes_in_python_images():
    # python:3.11-slim ships without procps; pgrep probes exit 127 and crash-loop pods.
    for path in [ROOT / "k8s" / "deployment.yaml",
                 *(ROOT / "k8s" / "helm" / "scraping-pipeline" / "templates").glob("*.yaml")]:
        if path.name == "kafka-delta-ingestor-deployment.yaml":
            continue  # separate (non-Python) image
        assert "pgrep" not in path.read_text(), path.name


# ---------------------------------------------------------------- #542
def _fake_proc(tmp_path, state):
    (tmp_path / "1").mkdir()
    (tmp_path / "1" / "stat").write_text(f"1 (python) {state} 0 1 1 0 -1")
    return tmp_path


@pytest.mark.parametrize("state,ok", [("S", True), ("R", True), ("Z", False), ("X", False)])
def test_container_pid1_state(tmp_path, monkeypatch, state, ok):
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.delenv("HEALTHCHECK_TCP", raising=False)
    assert probe.container_healthy(_fake_proc(tmp_path, state))[0] is ok


def test_container_missing_pid1_unhealthy(tmp_path, monkeypatch):
    monkeypatch.delenv("REDIS_HOST", raising=False)
    assert probe.container_healthy(tmp_path) == (False, "main process (PID 1) not running")


def test_container_redis_down_flips_unhealthy(tmp_path, monkeypatch):
    proc = _fake_proc(tmp_path, "S")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
    monkeypatch.setenv("REDIS_HOST", "127.0.0.1")
    monkeypatch.setenv("REDIS_PORT", str(dead_port))
    ok, why = probe.container_healthy(proc, timeout=0.5)
    assert not ok and "not answering PING" in why
    monkeypatch.setenv("HEALTHCHECK_REDIS", "0")
    assert probe.container_healthy(proc, timeout=0.5)[0] is True


def test_container_tcp_dependency(tmp_path, monkeypatch):
    proc = _fake_proc(tmp_path, "S")
    monkeypatch.delenv("REDIS_HOST", raising=False)
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        monkeypatch.setenv("HEALTHCHECK_TCP", f"127.0.0.1:{port}")
        assert probe.container_healthy(proc, timeout=1)[0] is True
    monkeypatch.setenv("HEALTHCHECK_TCP", f"127.0.0.1:{port},bogus")
    assert probe.container_healthy(proc, timeout=0.5)[0] is False


def test_container_cli_exit_codes(monkeypatch):
    monkeypatch.setattr(probe, "container_healthy", lambda: (False, "x"))
    assert probe.main(["container"]) == 1
    monkeypatch.setattr(probe, "container_healthy", lambda: (True, "ok"))
    assert probe.main(["container"]) == 0


def test_dockerfile_healthcheck_is_real_and_docs_match():
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "Dockerfile").read_text()
    assert 'CMD ["python", "-m", "src.utils.probe", "container"]' in dockerfile
    assert "sys.exit(0)" not in dockerfile
    for doc in ("README.md", "DEPLOYMENT.md"):
        assert "localhost:8000/health" not in (root / doc).read_text()
