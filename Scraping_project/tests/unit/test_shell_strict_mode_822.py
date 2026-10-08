"""#822: project bash scripts run in strict mode and stay correct under it.

Policy: every tracked ``*.sh`` (except temp_scripts/) starts with the standard
``set -euo pipefail``, diagnostics use ``set -uo pipefail``, and the sourced
compose_lib sets no options (scripts/SHELL_STANDARD.md).

Behaviour: the strict header must not break what the scripts do. Unset optional
env vars must not crash entrypoints (-u), and ``cmd | grep -q`` SIGPIPE must not
misreport a match (pipefail).
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import stat
import subprocess
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

STRICT = "set -euo pipefail"
DIAGNOSTIC = "set -uo pipefail"
DIAGNOSTIC_SCRIPTS = {"diagnose.sh", "scripts/diagnose_issues.sh", "scripts/smoke_local.sh"}
LIBRARIES = {"scripts/compose_lib.sh"}


def _scripts() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.sh"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    files = [f for f in out.stdout.split() if not f.startswith("temp_scripts/")]
    if not files:  # not a git checkout (sdist): fall back to the filesystem
        files = [
            str(p.relative_to(ROOT)) for p in ROOT.rglob("*.sh") if "temp_scripts" not in p.parts
        ]
    return sorted(files)


def _first_set_line(text: str) -> str | None:
    for line in text.splitlines():
        if re.match(r"^set -", line):
            return line.split("#", 1)[0].strip()
    return None


def test_inventory_covers_known_scripts():
    found = set(_scripts())
    for expected in (
        "rebuild_env.sh",
        "access_grafana.sh",
        "docker/entrypoints/crawler-entrypoint.sh",
        "scripts/complete_reset.sh",
        "scripts/compose_lib.sh",
    ):
        assert expected in found


@pytest.mark.parametrize("rel", _scripts())
def test_header_follows_standard(rel):
    text = (ROOT / rel).read_text()
    assert text.startswith("#!"), f"{rel}: missing shebang"
    first = _first_set_line(text)
    if rel in LIBRARIES:
        assert first is None, f"{rel}: a sourced library must not set shell options"
    elif rel in DIAGNOSTIC_SCRIPTS:
        assert first == DIAGNOSTIC, f"{rel}: diagnostics use {DIAGNOSTIC!r}, got {first!r}"
    else:
        assert first == STRICT, f"{rel}: expected {STRICT!r}, got {first!r}"


@pytest.mark.parametrize("rel", _scripts())
def test_bash_syntax(rel):
    r = subprocess.run([BASH, "-n", str(ROOT / rel)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("rel", _scripts())
def test_no_sigpipe_prone_grep_q_after_command(rel):
    """`cmd | grep -q` misreports under pipefail; capture first (here-strings are fine)."""
    bad = [
        line
        for line in (ROOT / rel).read_text().splitlines()
        if not line.lstrip().startswith("#")
        and re.search(r"\|\s*grep\s+-[a-zA-Z]*q", line)
    ]
    assert not bad, f"{rel}: {bad}"


@pytest.mark.parametrize("rel", _scripts())
def test_no_double_zero_count_idiom(rel):
    """`... | wc -l || echo 0` yields "0\\n0" under pipefail."""
    code = [
        line for line in (ROOT / rel).read_text().splitlines() if not line.lstrip().startswith("#")
    ]
    assert not [line for line in code if re.search(r"wc -l\s*\|\|\s*echo", line)], rel


# --- behaviour ---------------------------------------------------------------


def _stub(bin_dir: Path, name: str, body: str) -> None:
    p = bin_dir / name
    p.write_text(f"#!{BASH}\n{body}\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def bin_dir(tmp_path):
    d = tmp_path / "bin"
    d.mkdir()
    return d


@pytest.fixture
def tcp_port():
    """A listening localhost port standing in for Redis (accepts and closes)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    srv.settimeout(0.2)
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                conn.close()
            except OSError:
                pass

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    yield srv.getsockname()[1]
    stop.set()
    t.join(timeout=2)
    srv.close()


def _env(bin_dir: Path, **extra: str) -> dict[str, str]:
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", "HOME": str(bin_dir)}
    env.update(extra)
    return env


def _run(args, env, timeout=30):
    return subprocess.run(args, env=env, capture_output=True, text=True, timeout=timeout)


def test_kafka_ingest_entrypoint_runs_without_kafka_env(bin_dir):
    """-u: KAFKA_BOOTSTRAP_SERVERS unset (core profile) must not crash the entrypoint."""
    _stub(bin_dir, "kafka-delta-ingest", 'echo "ingest-started $*"')
    script = ROOT / "docker/entrypoints/kafka-delta-ingest-entrypoint.sh"
    r = _run([BASH, str(script), "--flag"], _env(bin_dir))
    assert r.returncode == 0, r.stderr
    assert "ingest-started --flag" in r.stdout


def test_metrics_exporter_entrypoint_runs_without_pythonpath(bin_dir, tcp_port):
    _stub(bin_dir, "python", 'echo "Python 3.11.9"')
    script = ROOT / "docker/entrypoints/metrics-exporter-entrypoint.sh"
    env = _env(bin_dir, REDIS_HOST="127.0.0.1", REDIS_PORT=str(tcp_port))
    r = _run([BASH, str(script), "echo", "exporter-up"], env)
    assert r.returncode == 0, r.stderr
    assert "exporter-up" in r.stdout
    assert "PYTHONPATH: " in r.stdout


def test_metrics_exporter_entrypoint_still_requires_redis_host(bin_dir):
    script = ROOT / "docker/entrypoints/metrics-exporter-entrypoint.sh"
    _stub(bin_dir, "python", 'echo "Python 3.11.9"')
    r = _run([BASH, str(script), "echo", "x"], _env(bin_dir, REDIS_PORT="6379"))
    assert r.returncode != 0
    assert "REDIS_HOST is not set" in r.stderr


def test_crawler_entrypoint_core_profile_under_strict_mode(bin_dir, tcp_port):
    _stub(bin_dir, "python", 'echo "Python 3.11.9"')
    script = ROOT / "docker/entrypoints/crawler-entrypoint.sh"
    env = _env(bin_dir, REDIS_HOST="127.0.0.1", REDIS_PORT=str(tcp_port))
    r = _run([BASH, str(script), "echo", "crawler-up"], env)
    assert r.returncode == 0, r.stderr
    assert "PROFILE: core" in r.stdout
    assert "crawler-up" in r.stdout


_BIG_PODS = (
    'echo "grafana-7d9f 1/1 Running 0 1d"\n'
    'for i in $(seq 1 20000); do echo "filler-pod-$i 0/1 Pending 0 1d padding padding"; done'
)


def test_sigpipe_pitfall_is_real(bin_dir):
    """Documents why scripts capture first: the naive pattern misreports a match."""
    _stub(bin_dir, "kubectl", _BIG_PODS)
    naive = 'set -o pipefail; if kubectl get pods | grep -q Running; then echo FOUND; else echo MISSED; fi'
    r = _run([BASH, "-c", naive], _env(bin_dir))
    assert r.stdout.strip() == "MISSED"


def test_access_grafana_detects_running_pod_despite_large_output(bin_dir):
    _stub(
        bin_dir,
        "kubectl",
        'if [ "$1" = "get" ]; then\n' + _BIG_PODS + '\nelse echo "port-forward $*"; fi',
    )
    r = _run([BASH, str(ROOT / "access_grafana.sh")], _env(bin_dir))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "not running" not in r.stdout
    assert "port-forward port-forward svc/grafana 3000:3000" in r.stdout


def test_access_grafana_reports_missing_pod(bin_dir):
    _stub(bin_dir, "kubectl", 'echo "No resources found"')
    r = _run([BASH, str(ROOT / "access_grafana.sh")], _env(bin_dir))
    assert r.returncode == 1
    assert "not running" in r.stdout


def test_compose_has_under_callers_strict_mode(bin_dir):
    lib = ROOT / "scripts/compose_lib.sh"
    prog = (
        f'set -euo pipefail; . "{lib}"; '
        'compose_has redis && echo has-redis; '
        'if compose_has kafka; then echo has-kafka; else echo no-kafka; fi'
    )
    r = _run([BASH, "-c", prog], _env(bin_dir, COMPOSE_SERVICES="scraper redis stage2-worker"))
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["has-redis", "no-kafka"]


def test_compose_lib_leaves_caller_options_alone(bin_dir):
    lib = ROOT / "scripts/compose_lib.sh"
    r = _run([BASH, "-c", f'. "{lib}"; echo "$-"; shopt -o pipefail | cat'], _env(bin_dir))
    assert "e" not in r.stdout.splitlines()[0]
    assert "off" in r.stdout


def test_diagnose_count_helper_prints_single_zero(bin_dir):
    text = (ROOT / "scripts/diagnose_issues.sh").read_text()
    helper = next(line for line in text.splitlines() if line.startswith("count_matches()"))
    prog = (
        f"set -uo pipefail; {helper}\n"
        "printf 'ok\\nfine\\n' | count_matches 'error'; "
        "printf 'ERROR a\\nok\\nerror b\\n' | count_matches 'error'; "
        "false | count_matches x"
    )
    r = _run([BASH, "-c", prog], _env(bin_dir))
    assert r.stdout.split() == ["0", "2", "0"]


# --- access_grafana.sh: local Compose vs Kubernetes (#382) ------------------------------


def test_access_grafana_uses_local_compose_when_no_k8s_pod(bin_dir):
    _stub(bin_dir, "kubectl", 'echo "No resources found"')
    _stub(bin_dir, "docker", 'if [ "$1 $2" = "compose version" ]; then echo v2; exit 0; fi\n'
                             'if [ "$2" = "ps" ]; then echo redis; echo grafana; fi')
    r = _run([BASH, str(ROOT / "access_grafana.sh")], _env(bin_dir))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Docker Compose" in r.stdout and "http://localhost:3000" in r.stdout
    assert "GRAFANA_ADMIN_PASSWORD" in r.stdout
    assert "port-forward" not in r.stdout


def test_access_grafana_local_flag_never_calls_kubectl(bin_dir):
    _stub(bin_dir, "kubectl", 'echo "kubectl-was-called" >&2; exit 9')
    _stub(bin_dir, "docker", 'if [ "$1 $2" = "compose version" ]; then exit 0; fi\n'
                             'if [ "$2" = "ps" ]; then echo grafana; fi')
    r = _run([BASH, str(ROOT / "access_grafana.sh"), "--local"], _env(bin_dir))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "kubectl-was-called" not in r.stderr


def test_access_grafana_explains_both_paths_when_nothing_runs(bin_dir):
    _stub(bin_dir, "kubectl", 'echo "No resources found"')
    _stub(bin_dir, "docker", 'if [ "$1 $2" = "compose version" ]; then exit 0; fi\nexit 0')
    r = _run([BASH, str(ROOT / "access_grafana.sh")], _env(bin_dir))
    assert r.returncode == 1
    assert "not running" in r.stdout
    assert "docker compose up -d grafana" in r.stdout and "--k8s" in r.stdout


def test_access_grafana_rejects_unknown_flag(bin_dir):
    r = _run([BASH, str(ROOT / "access_grafana.sh"), "--nope"], _env(bin_dir))
    assert r.returncode == 2
