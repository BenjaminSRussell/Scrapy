"""Destructive lake/queue ops: dry-run default, dual confirmation, prod break-glass, audit (#522, #573, #576)."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src.utils import destructive_guard as g

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def audit_log(tmp_path, monkeypatch):
    path = tmp_path / "audit" / "ops.jsonl"
    monkeypatch.setenv(g.AUDIT_ENV, str(path))
    for k in ("ENV", "APP_ENV", g.ALLOW_ENV):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("USER", "alice")
    return path


def records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def make_lake(base: Path) -> Path:
    from deltalake import write_deltalake
    import pyarrow as pa

    write_deltalake(str(base / "seed_urls"), pa.table({"url": ["a", "b", "c"]}))
    write_deltalake(str(base / "stage1" / "discovery"), pa.table({"url": ["x"]}))
    return base


def no_input(prompt):  # pragma: no cover - fails the test if a prompt happens
    raise AssertionError(f"unexpected prompt: {prompt}")


# --- policy ---------------------------------------------------------------------------

def test_default_is_dry_run_and_audited(audit_log):
    d = g.authorize("wipe", targets=[{"table": "t", "rows": 3}], confirm=False, input_fn=no_input, out=lambda s: None)
    assert not d.proceed and d.outcome == "dry_run" and d.exit_code == g.EXIT_DRY_RUN
    (rec,) = records(audit_log)
    assert rec["outcome"] == "dry_run" and rec["actor"] == "alice" and rec["targets"] == [{"table": "t", "rows": 3}]
    assert rec["env"] == "development" and rec["host"] and rec["ts"].endswith("+00:00")


def test_confirm_requires_typed_yes_interactively(audit_log):
    ok = g.authorize("wipe", targets=[], confirm=True, input_fn=lambda p: "yes", interactive=True, out=lambda s: None)
    assert ok.proceed and ok.outcome == "authorized"
    bad = g.authorize("wipe", targets=[], confirm=True, input_fn=lambda p: "y", interactive=True, out=lambda s: None)
    assert not bad.proceed and bad.exit_code == g.EXIT_REFUSED
    piped = g.authorize("wipe", targets=[], confirm=True, input_fn=no_input, interactive=False, out=lambda s: None)
    assert not piped.proceed and "not a terminal" in piped.reason
    assert [r["outcome"] for r in records(audit_log)] == ["authorized", "refused", "refused"]


def test_yes_only_with_allow_env(audit_log, monkeypatch):
    d = g.authorize("wipe", targets=[], confirm=True, assume_yes=True, input_fn=no_input, out=lambda s: None)
    assert not d.proceed and g.ALLOW_ENV in d.reason
    monkeypatch.setenv(g.ALLOW_ENV, "1")
    d = g.authorize("wipe", targets=[], confirm=True, assume_yes=True, input_fn=no_input, out=lambda s: None)
    assert d.proceed


@pytest.mark.parametrize("name", ["production", "PROD"])
def test_production_needs_break_glass_env_and_typed_phrase(audit_log, monkeypatch, name):
    monkeypatch.setenv("ENV", name)
    kw = dict(targets=[], confirm=True, interactive=True, out=lambda s: None)
    assert not g.authorize("wipe", break_glass=False, input_fn=no_input, **kw).proceed
    assert not g.authorize("wipe", break_glass=True, input_fn=no_input, **kw).proceed  # no ALLOW env
    monkeypatch.setenv(g.ALLOW_ENV, "1")
    # --yes is not a shortcut in production: the phrase is still required
    assert not g.authorize("wipe", break_glass=True, assume_yes=True, input_fn=lambda p: "yes", **kw).proceed
    d = g.authorize("wipe", break_glass=True, assume_yes=True, input_fn=lambda p: "production", **kw)
    assert d.proceed and "break-glass" in d.reason
    assert [r["outcome"] for r in records(audit_log)] == ["refused", "refused", "refused", "authorized"]


def test_app_env_fallback_and_actor_precedence():
    assert g.is_production({"APP_ENV": "production"})
    assert not g.is_production({"ENV": "staging", "APP_ENV": "production"})
    assert g.actor({"SUDO_USER": "root-caller", "USER": "root"}) == "root-caller"


def test_audit_write_failure_does_not_raise(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv(g.AUDIT_ENV, str(blocker / "sub" / "log.jsonl"))
    assert g.audit("wipe", "dry_run")["outcome"] == "dry_run"


def test_lake_targets_estimates_rows_and_bytes_without_scanning(tmp_path):
    targets = {t["table"]: t for t in g.lake_targets(make_lake(tmp_path / "lake"))}
    assert targets["seed_urls"]["rows"] == 3 and targets["seed_urls"]["bytes"] > 0
    assert targets[str(Path("stage1") / "discovery")]["rows"] == 1
    assert g.lake_targets(tmp_path / "missing") == []
    assert "3 rows" in g.format_targets(list(targets.values()))


def test_guarded_wipe_backs_up_then_deletes(tmp_path, audit_log, monkeypatch):
    lake = make_lake(tmp_path / "lake")
    args = argparse.Namespace(confirm=False, yes=False, break_glass=False, backup_dir=tmp_path / "bk")
    assert g.guarded_lake_wipe(lake, args, input_fn=no_input).exit_code == g.EXIT_DRY_RUN
    assert (lake / "seed_urls").exists()

    monkeypatch.setenv(g.ALLOW_ENV, "1")
    args.confirm = args.yes = True
    assert g.guarded_lake_wipe(lake, args, input_fn=no_input).proceed
    assert not lake.exists()
    (backup,) = list((tmp_path / "bk").iterdir())
    assert g.lake_targets(backup)[0]["rows"] == 3  # restorable copy
    rec = records(audit_log)[-1]
    assert rec["outcome"] == "completed" and rec["backup"] == str(backup)


def test_legacy_force_means_confirm_yes_but_still_needs_allow_env(tmp_path, audit_log):
    lake = make_lake(tmp_path / "lake")
    d = g.guarded_lake_wipe(lake, argparse.Namespace(force=True), input_fn=no_input)
    assert not d.proceed and d.outcome == "refused" and lake.exists()


# --- entry points ---------------------------------------------------------------------

def _load(rel: str):
    spec = importlib.util.spec_from_file_location(f"_guard_{Path(rel).stem}", ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("argv,code", [([], 2), (["--force"], 3), (["--seed-only"], 2)])
def test_reset_lake_script_does_nothing_without_confirmation(tmp_path, audit_log, monkeypatch, argv, code):
    mod = _load("scripts/reset_lake.py")
    lake = make_lake(tmp_path / "lake")
    monkeypatch.setattr(mod, "DELTA_LAKE", lake)
    monkeypatch.setattr(mod, "seed_lake", lambda: pytest.fail("must not seed"))
    monkeypatch.setattr(sys, "argv", ["reset_lake.py", *argv])
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == code and (lake / "seed_urls").exists()


def test_reset_lake_script_automation_path(tmp_path, audit_log, monkeypatch):
    mod = _load("scripts/reset_lake.py")
    lake = make_lake(tmp_path / "lake")
    seeded = []
    monkeypatch.setattr(mod, "DELTA_LAKE", lake)
    monkeypatch.setattr(mod, "seed_lake", lambda: seeded.append(True))
    monkeypatch.setenv(g.ALLOW_ENV, "1")
    monkeypatch.setattr(sys, "argv", ["reset_lake.py", "--confirm", "--yes"])
    mod.main()
    assert seeded and not lake.exists()
    assert [r["outcome"] for r in records(audit_log)] == ["authorized", "completed"]


def test_cli_reset_and_reseed_clear_are_guarded(tmp_path, audit_log, monkeypatch):
    import src.core.constants as constants

    lake = make_lake(tmp_path / "lake")
    monkeypatch.setattr(constants, "DELTA_LAKE", lake)
    cli = _load("cli.py")
    with pytest.raises(SystemExit) as exc:
        cli.cmd_reset(argparse.Namespace(force=False, confirm=False, yes=False, break_glass=False, backup_dir=None))
    assert exc.value.code == 2 and lake.exists()

    reseed = _load("reseed.py")
    monkeypatch.setattr(reseed, "DELTA_LAKE", lake)
    monkeypatch.setattr(reseed, "get_delta_manager", lambda **k: pytest.fail("must not seed"))
    csv = tmp_path / "seeds.csv"
    csv.write_text("https://a.example\n")
    monkeypatch.setattr(sys, "argv", ["reseed.py", "--csv", str(csv), "--clear"])
    with pytest.raises(SystemExit) as exc:
        reseed.main()
    assert exc.value.code == 2 and (lake / "seed_urls").exists()


@pytest.fixture
def drain(monkeypatch):
    """Real LakeDrainer + config.yml queues on fakeredis (the old import of the removed
    src.common.config/redis_manager made the script and the preStop hook a silent no-op)."""
    fakeredis = pytest.importorskip("fakeredis")
    mod = _load("drain_lake.py")
    client = fakeredis.FakeRedis()
    client.rpush("stage1_discovered_urls", "a", "b", "c", "d", "e")  # transient (list)
    client.rpush("stage2_large_docs", *"1234567")  # persistent (list)
    client.zadd(mod.JS_PRIORITY_QUEUE, {"https://js.example": 1.0})  # zset
    monkeypatch.setattr(mod, "RedisHelper", lambda **kw: type("H", (), {"client": client})())
    return mod, client


def test_drain_lake_imports_and_reads_typed_queues(drain):
    mod, client = drain
    d = mod.LakeDrainer()
    assert {"stage2_large_docs"} <= d.persistent_queues and "stage1_discovered_urls" in d.transient_queues
    assert d.redis.get_all_queue_stats() == {"stage1_discovered_urls": 5, "stage2_large_docs": 7}
    assert d.redis.get_queue_size() == 1
    assert d.drain_targets("all") == [{"queue": "stage1_discovered_urls", "rows": 5, "persistent": False},
                                      {"queue": "priority_queue", "rows": 1, "persistent": False}]


def test_drain_cli_needs_confirm(drain, audit_log, monkeypatch, capsys):
    mod, client = drain
    monkeypatch.setattr(sys, "argv", ["drain_lake.py", "--drain-transient"])
    assert mod.main() == 2 and client.llen("stage1_discovered_urls") == 5
    assert "stage1_discovered_urls: 5 rows" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["drain_lake.py", "--drain-transient", "--dry-run"])
    assert mod.main() == 0 and client.llen("stage1_discovered_urls") == 5
    monkeypatch.setenv(g.ALLOW_ENV, "1")
    monkeypatch.setattr(sys, "argv", ["drain_lake.py", "--queue", "stage2_large_docs", "--confirm", "--yes"])
    assert mod.main() == 0 and not client.exists("stage2_large_docs")  # no second prompt after the guard
    assert client.llen("stage1_discovered_urls") == 5
    assert records(audit_log)[-1]["outcome"] == "completed"


def test_drain_cli_production_refused_without_break_glass(drain, audit_log, monkeypatch):
    mod, client = drain
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv(g.ALLOW_ENV, "1")
    monkeypatch.setattr(sys, "argv", ["drain_lake.py", "--drain-all", "--include-persistent", "--confirm"])
    assert mod.main() == 3 and client.llen("stage2_large_docs") == 7


def test_prestop_hook_library_call_is_unguarded(drain):
    mod, client = drain
    mod.LakeDrainer().drain_transient_queues()  # what the Helm preStop hook runs
    assert not client.exists("stage1_discovered_urls") and client.llen("stage2_large_docs") == 7


# --- complete_reset.sh ----------------------------------------------------------------

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")


def _reset_sh(tmp_path, *args, **env):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    marker = tmp_path / "docker_called"
    docker = fake_bin / "docker"
    docker.write_text(f"#!/bin/sh\necho \"$@\" >> {marker}\n")
    docker.chmod(0o755)
    full_env = {k: v for k, v in os.environ.items() if k not in ("ENV", "APP_ENV", g.ALLOW_ENV)}
    full_env.update(COMPOSE_SERVICES="redis scraper grafana", PATH=f"{fake_bin}:{os.environ['PATH']}",
                    DESTRUCTIVE_AUDIT_LOG=str(tmp_path / "audit.jsonl"), **env)
    r = subprocess.run(["bash", "scripts/complete_reset.sh", *args], cwd=ROOT, env=full_env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=30, check=False)
    return r, marker.exists(), records(tmp_path / "audit.jsonl")


@needs_bash
def test_complete_reset_without_flags_is_a_noop_error(tmp_path):
    r, docker_called, recs = _reset_sh(tmp_path)
    assert r.returncode == 2 and not docker_called
    assert "infra: redis" in r.stdout and "DRY RUN" in r.stdout + r.stderr
    assert recs[-1]["outcome"] == "dry_run" and recs[-1]["actor"]


@needs_bash
@pytest.mark.parametrize("args,env,needle", [
    (["--yes"], {}, "ALLOW_LAKE_RESET=1"),
    (["--confirm"], {}, "not a terminal"),
    (["--confirm", "--yes"], {"ENV": "production", "ALLOW_LAKE_RESET": "1"}, "production"),
    (["--confirm", "--i-know-what-im-doing"], {"ENV": "Production"}, "ALLOW_LAKE_RESET=1"),
])
def test_complete_reset_refusals(tmp_path, args, env, needle):
    r, docker_called, recs = _reset_sh(tmp_path, *args, **env)
    assert r.returncode == 3 and not docker_called, r.stdout + r.stderr
    assert needle in r.stdout + r.stderr
    assert recs[-1]["outcome"] == "refused"


@needs_bash
def test_complete_reset_dry_run_flag_still_exits_zero(tmp_path):
    r, docker_called, _ = _reset_sh(tmp_path, "--dry-run")
    assert r.returncode == 0 and not docker_called


def test_scripts_readme_documents_the_policy():
    text = (ROOT / "scripts" / "README.md").read_text(encoding="utf-8")
    for needle in ("--confirm", "ALLOW_LAKE_RESET", "--i-know-what-im-doing", "destructive_ops.jsonl", "--backup-dir"):
        assert needle in text
