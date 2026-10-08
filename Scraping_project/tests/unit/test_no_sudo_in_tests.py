"""#567: tests must not shell out through sudo (rootless Docker / CI without NOPASSWD)."""

import re
import sys
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parents[1]
SUDO_CALL = re.compile(r"""["'\[]\s*sudo\b|\bsudo\s+docker\b""")


def test_no_test_invokes_sudo():
    offenders = []
    for path in TESTS.rglob("*.py"):
        if path.name == Path(__file__).name:
            continue
        for lineno, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if SUDO_CALL.search(line):
                offenders.append(f"{path.relative_to(TESTS)}:{lineno}: {line.strip()}")
    assert not offenders, "sudo in tests:\n" + "\n".join(offenders)


def test_online_alert_test_skips_cleanly_without_docker(monkeypatch):
    sys.path.insert(0, str(TESTS / "observability"))
    try:
        import test_alert_intervals as tai
    finally:
        sys.path.pop(0)

    monkeypatch.setattr(tai.shutil, "which", lambda name: None)
    assert tai.docker_unavailable_reason() == "docker CLI not installed"

    monkeypatch.setattr(tai.shutil, "which", lambda name: "/usr/bin/docker")
    denied = mock.Mock(returncode=1, stderr="permission denied while trying to connect to the Docker daemon socket")
    monkeypatch.setattr(tai.subprocess, "run", lambda *a, **k: denied)
    assert "not reachable without sudo" in tai.docker_unavailable_reason()

    monkeypatch.delenv("OBS_OFFLINE", raising=False)
    import unittest

    try:
        tai.TestAlertIntervals.setUpClass()
    except unittest.SkipTest as e:
        assert "skipped" in str(e)
    else:  # pragma: no cover
        raise AssertionError("expected SkipTest")
    tai.TestAlertIntervals.tearDownClass()  # no-op: stack never started


def test_compose_runs_without_sudo_or_shell(monkeypatch):
    sys.path.insert(0, str(TESTS / "observability"))
    try:
        import test_alert_intervals as tai
    finally:
        sys.path.pop(0)
    calls = []
    monkeypatch.setattr(tai.subprocess, "run", lambda argv, **k: calls.append((argv, k)) or mock.Mock(returncode=0))
    tai.compose("up", "-d", "grafana")
    argv, kwargs = calls[0]
    assert argv[:2] == ["docker", "compose"] and "sudo" not in argv
    assert not kwargs.get("shell")
