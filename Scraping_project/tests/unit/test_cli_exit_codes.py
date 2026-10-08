"""cli.py / start.py exit codes, help and bad arguments (#297). No live services."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import cli

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = ["scrapy", "deep_dive", "pipeline", "drain", "export", "health", "setup", "reset", "clean", "validate", "data", "seeds"]


def _run_main(*argv):
    with patch.object(sys, "argv", ["cli.py", *argv]), pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


def _proc(script, *argv, env=None):
    return subprocess.run(
        [sys.executable, str(ROOT / script), *argv],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def test_top_level_help_lists_every_command(capsys):
    assert _run_main("--help") == 0
    out = capsys.readouterr().out
    for name in COMMANDS:
        assert name in out


@pytest.mark.parametrize(
    "argv",
    [[c, "--help"] for c in COMMANDS] + [["data", "gc", "--help"]] + [["seeds", s, "--help"] for s in ("list", "add", "disable", "audit")],
    ids=lambda a: " ".join(a[:-1]),
)
def test_every_subcommand_help_exits_zero(argv, capsys):
    assert _run_main(*argv) == 0
    assert "usage:" in capsys.readouterr().out


def test_no_command_prints_help_and_fails(capsys):
    assert _run_main() == 1
    assert "usage:" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv,needle",
    [
        (["nope"], "invalid choice"),
        (["pipeline", "--stage2-workers", "many"], "invalid int value"),
        (["export", "--format", "xml"], "invalid choice"),
        (["data", "gc", "--ttl-days", "soon"], "invalid float value"),
        (["seeds"], "required"),
        (["seeds", "add"], "required"),
        (["health", "--bogus"], "unrecognized arguments"),
    ],
)
def test_bad_arguments_are_usage_errors(argv, needle, capsys):
    assert _run_main(*argv) == 2
    assert needle in capsys.readouterr().err


def test_command_group_without_subcommand_shows_group_usage(capsys):
    # Used to fall through to args.func and log an AttributeError traceback with exit 1.
    assert _run_main("data") == 2
    err = capsys.readouterr().err
    assert "gc" in err and "usage:" in err
    assert "AttributeError" not in err


def test_handler_success_failure_and_interrupt_exit_codes():
    def ok(args):
        return None

    def boom(args):
        raise RuntimeError("lake unavailable")

    def interrupted(args):
        raise KeyboardInterrupt

    with patch.object(cli, "cmd_health", ok):
        assert _run_main("health") == 0
    with patch.object(cli, "cmd_health", boom):
        assert _run_main("health") == 1
    with patch.object(cli, "cmd_health", interrupted):
        assert _run_main("health") == 130


def test_cli_process_exit_codes():
    assert _proc("cli.py", "--help").returncode == 0
    bad = _proc("cli.py", "nope")
    assert bad.returncode == 2 and "invalid choice" in bad.stderr


def test_start_help_and_bad_args():
    ok = _proc("start.py", "--help")
    assert ok.returncode == 0 and "--env" in ok.stdout
    bad = _proc("start.py", "--env", "mars")
    assert bad.returncode == 2 and "invalid choice" in bad.stderr
    assert _proc("start.py", "--wait-timeout", "soon").returncode == 2


def test_start_reports_missing_tools_without_touching_docker(tmp_path):
    # Empty PATH: docker/docker-compose (local) and kubectl/helm (k8s) are all "missing".
    for env_name, tools in (("local", "docker"), ("k8s", "kubectl")):
        res = _proc("start.py", "--env", env_name, env={"PATH": str(tmp_path)})
        assert res.returncode == 1
        assert "Missing required tooling" in res.stderr and tools in res.stderr
