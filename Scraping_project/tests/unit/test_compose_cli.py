"""#342: start.py / shutdown.py work with the `docker compose` v2 plugin, not only docker-compose."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

import compose_cli

pytestmark = [pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[2]


def _which(*present: str):
    return lambda tool: f"/usr/bin/{tool}" if tool in present else None


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.delenv("COMPOSE_CMD", raising=False)


def _plugin(monkeypatch, works: bool):
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0 if works else 1, "Docker Compose version v2", "")

    monkeypatch.setattr(compose_cli.subprocess, "run", fake_run)
    return calls


def test_legacy_binary_preferred_when_present(monkeypatch):
    monkeypatch.setattr(compose_cli.shutil, "which", _which("docker", "docker-compose"))
    calls = _plugin(monkeypatch, works=True)
    assert compose_cli.compose_cmd() == ("docker-compose",)
    assert calls == []  # no probe needed


def test_plugin_used_when_only_docker_is_installed(monkeypatch):
    monkeypatch.setattr(compose_cli.shutil, "which", _which("docker"))
    calls = _plugin(monkeypatch, works=True)
    assert compose_cli.compose_cmd() == ("docker", "compose")
    assert calls == [["docker", "compose", "version"]]
    assert compose_cli.compose_available() is True
    assert compose_cli.compose_str() == "docker compose"


def test_docker_without_plugin_is_reported_missing(monkeypatch):
    monkeypatch.setattr(compose_cli.shutil, "which", _which("docker"))
    _plugin(monkeypatch, works=False)
    assert compose_cli.compose_cmd() == ("docker-compose",)
    assert compose_cli.compose_available() is False


def test_dry_run_never_probes(monkeypatch):
    monkeypatch.setattr(compose_cli.shutil, "which", _which("docker"))
    calls = _plugin(monkeypatch, works=False)
    assert compose_cli.compose_cmd(probe=False) == ("docker", "compose")
    assert calls == []


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("COMPOSE_CMD", "podman compose")
    monkeypatch.setattr(compose_cli.shutil, "which", _which("podman", "docker-compose"))
    assert compose_cli.compose_cmd() == ("podman", "compose")
    assert compose_cli.compose_available() is True


def test_probe_errors_are_not_fatal(monkeypatch):
    monkeypatch.setattr(compose_cli.shutil, "which", _which("docker"))

    def broken(*a, **k):
        raise OSError("exec format error")

    monkeypatch.setattr(compose_cli.subprocess, "run", broken)
    assert compose_cli.compose_cmd() == ("docker-compose",)


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_start_runs_the_plugin_when_that_is_all_there_is(monkeypatch, capsys):
    start = _load("_ops_start_342", "start.py")
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(compose_cli.shutil, "which", _which("docker"))
    _plugin(monkeypatch, works=True)
    ran: list[tuple] = []

    def fake_run(cmd, *a, **k):
        ran.append(tuple(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(start.subprocess, "run", fake_run)
    assert start.missing_tools("local") == []
    assert start.main(["--env", "local"]) == 0
    assert ("docker", "compose", "up", "-d") in ran
    out = capsys.readouterr().out
    assert "docker compose logs -f" in out and "docker-compose" not in out.replace("docker-compose.yml", "")


def test_start_names_both_options_when_neither_exists(monkeypatch):
    start = _load("_ops_start_342b", "start.py")
    monkeypatch.setattr(compose_cli.shutil, "which", _which())
    assert start.missing_tools("local") == ["docker", compose_cli.MISSING_HINT]


def test_shutdown_uses_the_plugin(monkeypatch):
    shutdown = _load("_ops_shutdown_342", "shutdown.py")
    monkeypatch.setattr(compose_cli.shutil, "which", _which("docker"))
    _plugin(monkeypatch, works=True)
    shutdown.ensure_tools_available("local")  # no SystemExit
    ran: list[tuple] = []
    monkeypatch.setattr(shutdown, "run_command", lambda cmd: ran.append(tuple(cmd)))
    src = (ROOT / "shutdown.py").read_text(encoding="utf-8")
    assert '"docker-compose", "down"' not in src and '"docker-compose", "images"' not in src


def test_shutdown_exits_when_no_compose(monkeypatch):
    shutdown = _load("_ops_shutdown_342b", "shutdown.py")
    monkeypatch.setattr(compose_cli.shutil, "which", _which())
    with pytest.raises(SystemExit):
        shutdown.ensure_tools_available("local")


def test_contributing_documents_every_cli_subcommand():
    """#345: cli.py is discoverable from CONTRIBUTING; no phantom load_seeds command."""
    import re

    help_text = subprocess.run(
        [__import__("sys").executable, str(ROOT / "cli.py"), "--help"],
        capture_output=True, text=True, cwd=ROOT, timeout=60,
    ).stdout
    m = re.search(r"\{([a-z_,-]+)\}", help_text)
    assert m, help_text
    contributing = (ROOT.parent / "CONTRIBUTING.md").read_text(encoding="utf-8")
    missing = [c for c in m.group(1).split(",") if f"python cli.py {c}" not in contributing]
    assert missing == []
    assert "There is no `cli.py load_seeds`" in contributing
    assert "cli-help:" in (ROOT / "Makefile").read_text(encoding="utf-8")


def test_env_example_covers_entrypoint_requirements():
    """#239: every variable the crawler entrypoint requires is in .env.example."""
    import re

    entry = (ROOT / "docker/entrypoints/crawler-entrypoint.sh").read_text(encoding="utf-8")
    required = set(re.findall(r':\s*"\$\{([A-Z_]+):\?', entry))
    assert {"REDIS_HOST", "REDIS_PORT"} <= required
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for var in required:
        assert re.search(rf"^{var}=", example, re.M), var
    for var in ("KAFKA_BOOTSTRAP_SERVERS", "REQUIRE_KAFKA", "GRAFANA_ADMIN_PASSWORD", "DB_PASSWORD",
                "POSTGRES_PASSWORD", "WORKER_METRICS_PORT", "REDIS_PASSWORD"):
        assert var in example, var
    assert "[required]" in example and "[optional]" in example
    # every ${VAR} docker-compose.yml interpolates is documented
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for var in set(re.findall(r"\$\{([A-Z_]+)[:}]", compose)):
        assert var in example, f"{var} used by docker-compose.yml but missing from .env.example"


def test_kafka_delta_ingest_env_example_matches_the_binary():
    """#379: the README's .env.example exists and lists what main.rs actually reads."""
    import re

    kdi = ROOT / "kafka-delta-ingest"
    example = (kdi / ".env.example").read_text(encoding="utf-8")
    used = set(re.findall(r'std::env::var\("([A-Z_]+)"\)', (kdi / "src" / "main.rs").read_text(encoding="utf-8")))
    assert used and all(re.search(rf"^{v}=", example, re.M) for v in used), used
    readme = (kdi / "README.md").read_text(encoding="utf-8")
    assert "apt-get install" in readme and "build-essential" in readme  # Linux prerequisites
    assert "cp .env.example .env" in readme


def test_compose_superuser_password_is_overridable():
    """#482: the compose Postgres superuser password comes from .env, not a literal."""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=postgres\n" not in compose
    assert "postgres:postgres@" not in compose
    assert compose.count("${POSTGRES_PASSWORD:-postgres}") == 3


def test_security_policy_covers_secret_handling_and_rotation():
    sec = (ROOT.parent / "SECURITY.md").read_text(encoding="utf-8")
    assert "## Handling secrets" in sec and "Rotating a leaked or default password" in sec
    assert "ALTER ROLE" in sec
    readme = (ROOT.parent / "README.md").read_text(encoding="utf-8")
    assert "## 🔒 Security" in readme and "(SECURITY.md)" in readme
