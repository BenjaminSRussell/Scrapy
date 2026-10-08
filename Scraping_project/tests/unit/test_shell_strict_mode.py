"""#822: project bash scripts use the documented strict-mode header and work under it."""

import os
import re
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]  # Scraping_project
REPO = ROOT.parent
LIB = ROOT / "scripts" / "compose_lib.sh"
DIAGNOSTICS = {"diagnose.sh", "scripts/diagnose_issues.sh"}
LIBRARIES = {"scripts/compose_lib.sh"}

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")


def _supported_scripts():
    out = []
    for p in sorted(ROOT.rglob("*.sh")):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith(("temp_scripts/", "data/")) or "/node_modules/" in rel:
            continue
        out.append(rel)
    return out


def _set_line(text):
    m = re.search(r"^\s*set (-[a-z]+(?: -o \w+)*|-[a-z]+o pipefail)\b.*$", text, re.M)
    return m.group(0).split("#")[0].strip() if m else None


@pytest.mark.parametrize("rel", _supported_scripts())
def test_header_matches_standard(rel):
    text = (ROOT / rel).read_text()
    assert text.startswith(("#!/usr/bin/env bash", "#!/bin/bash")), rel
    line = _set_line(text)
    if rel in LIBRARIES:
        assert line is None, f"{rel} is sourced and must not change the caller's options"
    elif rel in DIAGNOSTICS:
        assert line == "set -uo pipefail", f"{rel}: diagnostics use `set -uo pipefail` (got {line!r})"
    else:
        assert line == "set -euo pipefail", f"{rel}: expected `set -euo pipefail` (got {line!r})"


@needs_bash
@pytest.mark.parametrize("rel", _supported_scripts())
def test_scripts_parse(rel):
    assert subprocess.run(["bash", "-n", str(ROOT / rel)], capture_output=True).returncode == 0


def _strict_lib(snippet, services):
    env = {**os.environ, "COMPOSE_SERVICES": services}
    return subprocess.run(["bash", "-c", f'set -euo pipefail; . "{LIB}"; {snippet}'], env=env,
                          capture_output=True, text=True, timeout=60)


@needs_bash
def test_compose_has_reliable_under_pipefail():
    # Target first in a long list: `producer | grep -q` would SIGPIPE the producer.
    services = "redis " + " ".join(f"svc{i}" for i in range(12000))  # ~105KB: > 64KB pipe buffer, < 128KB env limit
    for _ in range(5):
        assert _strict_lib("compose_has redis", services).returncode == 0
    assert _strict_lib("compose_has nope", services).returncode == 1


@needs_bash
def test_compose_filter_all_missing_under_set_u():
    r = _strict_lib('out="$(compose_filter kafka zookeeper)"; printf "[%s]" "$out"', "redis")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "[]"
    assert "not defined" in r.stderr


def _stub_path(tmp_path, name):
    stub = tmp_path / name
    stub.write_text("#!/usr/bin/env bash\necho STUB-RAN \"$@\"\n")
    stub.chmod(0o755)
    return f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}"


def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if k not in {"KAFKA_BOOTSTRAP_SERVERS", "PYTHONPATH", "REDIS_HOST", "REDIS_PORT"}}
    env.update(extra)
    return env


@needs_bash
def test_kdi_entrypoint_runs_with_kafka_unset(tmp_path):
    script = ROOT / "docker/entrypoints/kafka-delta-ingest-entrypoint.sh"
    env = _clean_env(PATH=_stub_path(tmp_path, "kafka-delta-ingest"))
    r = subprocess.run(["bash", str(script), "--flag", "x"], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "STUB-RAN --flag x" in r.stdout


@needs_bash
def test_metrics_entrypoint_runs_with_pythonpath_unset(tmp_path):
    script = ROOT / "docker/entrypoints/metrics-exporter-entrypoint.sh"
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(8)
        port = srv.getsockname()[1]
        env = _clean_env(REDIS_HOST="127.0.0.1", REDIS_PORT=str(port))
        r = subprocess.run(["bash", str(script), "echo", "STARTED"], env=env, capture_output=True,
                           text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "STARTED" in r.stdout and "PYTHONPATH: " in r.stdout
