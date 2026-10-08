"""#522 / #576: destructive lake operations are dry-run by default, need dual
confirmation, refuse production without break-glass + typed confirmation, and
are audited."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from src.utils import destructive_guard as g
from src.utils.lake_inventory import describe_table, inventory

PROJECT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT / rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def audit_log(tmp_path, monkeypatch):
    """Every test audits to a temp file, never data/logs/."""
    path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("LAKE_AUDIT_LOG", str(path))
    for var in ("ALLOW_LAKE_RESET", "ENV", "DELTA_ALLOW_HARD_DELETE"):
        monkeypatch.delenv(var, raising=False)
    return path


def records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "lake"
    for name, n in (("stage2_queue", 5), ("js_spider_queue", 2), ("stage4_large_docs", 3), ("seed_urls", 4)):
        write_deltalake(str(root / name), pa.table({"url": [f"u{i}" for i in range(n)]}))
    monkeypatch.setenv("DELTA_LAKE_PATH", str(root))
    return root


# ---------------------------------------------------------------- authorize


def test_dry_run_is_default_and_audited(audit_log):
    assert g.authorize("op", ["t"], execute=False, i_really_mean_it=True) is False
    (rec,) = records(audit_log)
    assert rec["outcome"] == "dry_run" and rec["dry_run"] is True and rec["targets"] == ["t"]


@pytest.mark.parametrize(
    "flag,env,missing",
    [
        (False, {}, ["--i-really-mean-it", "ALLOW_LAKE_RESET=1"]),
        (True, {}, ["ALLOW_LAKE_RESET=1"]),
        (False, {"ALLOW_LAKE_RESET": "1"}, ["--i-really-mean-it"]),
        (True, {"ALLOW_LAKE_RESET": "yes"}, ["ALLOW_LAKE_RESET=1"]),
    ],
)
def test_execute_needs_flag_and_env(audit_log, flag, env, missing):
    with pytest.raises(g.DestructiveOpRefused):
        g.authorize("op", ["t"], execute=True, i_really_mean_it=flag, environ=env)
    rec = records(audit_log)[-1]
    assert rec["outcome"] == "refused" and rec["details"]["missing"] == missing


def test_dual_confirmation_authorizes_outside_production(audit_log):
    env = {"ALLOW_LAKE_RESET": "1", "ENV": "development"}
    assert g.authorize("op", ["t"], execute=True, i_really_mean_it=True, environ=env, input_fn=pytest.fail)
    rec = records(audit_log)[-1]
    assert rec["outcome"] == "authorized"
    for key in ("at", "actor", "host", "pid", "argv", "env"):
        assert rec[key] not in (None, "")


@pytest.mark.parametrize("env_name", ["production", "prod", "PRODUCTION"])
def test_production_needs_break_glass(audit_log, env_name):
    env = {"ALLOW_LAKE_RESET": "1", "ENV": env_name}
    with pytest.raises(g.DestructiveOpRefused, match="--break-glass"):
        g.authorize("op", ["t"], execute=True, i_really_mean_it=True, environ=env)


def test_production_needs_tty_and_exact_typed_confirmation(audit_log):
    env = {"ALLOW_LAKE_RESET": "1", "ENV": "production"}
    kw = dict(execute=True, i_really_mean_it=True, break_glass=True, environ=env)
    with pytest.raises(g.DestructiveOpRefused, match="interactive"):
        g.authorize("reset-lake", ["t"], interactive=False, **kw)
    with pytest.raises(g.DestructiveOpRefused, match="did not match"):
        g.authorize("reset-lake", ["t"], interactive=True, input_fn=lambda _: "yes", **kw)
    assert g.authorize("reset-lake", ["t"], interactive=True, input_fn=lambda _: "reset-lake production", **kw)
    assert [r["outcome"] for r in records(audit_log)] == ["refused", "refused", "authorized"]


def test_audit_survives_unwritable_log(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("LAKE_AUDIT_LOG", str(blocker / "audit.jsonl"))  # parent is a file
    rec = g.audit("op", ["t"], "dry_run", dry_run=True)
    assert rec["operation"] == "op"


def test_default_audit_log_is_outside_the_lake(monkeypatch):
    monkeypatch.delenv("LAKE_AUDIT_LOG", raising=False)
    assert g.audit_path().parts[-2:] == ("logs", "destructive_ops.jsonl")


def test_guard_cli_exit_codes(audit_log, monkeypatch):
    assert g.main(["op", "t"]) == g.DRY_RUN_EXIT
    assert g.main(["op", "t", "--execute", "--i-really-mean-it"]) == g.REFUSED_EXIT
    monkeypatch.setenv("ALLOW_LAKE_RESET", "1")
    assert g.main(["op", "t", "--execute", "--i-really-mean-it"]) == 0


# ---------------------------------------------------------------- inventory


def test_inventory_reports_versions_and_rows(lake):
    by_name = {t.name: t for t in inventory(lake)}
    assert by_name["stage2_queue"].rows == 5 and by_name["stage2_queue"].version == 0


def test_inventory_of_emptied_table_does_not_panic(lake):
    DeltaTable(str(lake / "stage2_queue")).delete()
    info = describe_table(lake / "stage2_queue")  # deltalake panics in get_add_actions here
    assert info.rows == 0 and info.version == 1


# ---------------------------------------------------------------- drain_lake


@pytest.fixture
def drain():
    return _load("drain_lake_under_test", "drain_lake.py")


def test_drain_default_is_dry_run(drain, lake, audit_log):
    assert drain.main([]) == 0
    assert DeltaTable(str(lake / "stage2_queue")).to_pyarrow_table().num_rows == 5
    assert records(audit_log)[-1]["outcome"] == "dry_run"


def test_drain_refused_without_env(drain, lake, audit_log):
    assert drain.main(["--execute", "--i-really-mean-it"]) == 3
    assert DeltaTable(str(lake / "stage2_queue")).to_pyarrow_table().num_rows == 5


def test_drain_transient_then_restore(drain, lake, audit_log, monkeypatch, capsys):
    monkeypatch.setenv("ALLOW_LAKE_RESET", "1")
    assert drain.main(["--execute", "--i-really-mean-it"]) == 0
    for name in ("stage2_queue", "js_spider_queue"):
        assert DeltaTable(str(lake / name)).to_pyarrow_table().num_rows == 0
    assert DeltaTable(str(lake / "stage4_large_docs")).to_pyarrow_table().num_rows == 3  # persistent untouched
    assert "--restore stage2_queue --to-version 0" in capsys.readouterr().out
    done = records(audit_log)[-1]
    assert done["outcome"] == "executed"
    assert done["details"]["results"]["stage2_queue"]["version_before"] == 0

    assert drain.main(["--restore", "stage2_queue", "--to-version", "0", "--execute", "--i-really-mean-it"]) == 0
    assert DeltaTable(str(lake / "stage2_queue")).to_pyarrow_table().num_rows == 5


def test_persistent_queue_needs_include_persistent(drain, lake, monkeypatch):
    monkeypatch.setenv("ALLOW_LAKE_RESET", "1")
    with pytest.raises(SystemExit, match="persistent"):
        drain.main(["-q", "stage4_large_docs", "--execute", "--i-really-mean-it"])
    assert drain.main(["-q", "stage4_large_docs", "--include-persistent", "--execute", "--i-really-mean-it"]) == 0
    assert DeltaTable(str(lake / "stage4_large_docs")).to_pyarrow_table().num_rows == 0


def test_non_queue_table_rejected(drain, lake):
    with pytest.raises(SystemExit, match="not a queue table"):
        drain.main(["-q", "stage2_page_analysis"])


def test_cli_drain_forwards_args_and_exit_code(lake, audit_log):
    env = {**os.environ, "DELTA_LAKE_PATH": str(lake), "LAKE_AUDIT_LOG": str(audit_log)}
    env.pop("ALLOW_LAKE_RESET", None)
    out = subprocess.run([sys.executable, "cli.py", "drain", "--execute", "--i-really-mean-it"], cwd=PROJECT, env=env, capture_output=True, text=True)
    assert out.returncode == 3, out.stderr[-500:]
    out = subprocess.run([sys.executable, "cli.py", "drain", "--list"], cwd=PROJECT, env=env, capture_output=True, text=True)
    assert out.returncode == 0 and "stage2_queue" in out.stdout


# ---------------------------------------------------------------- reset_lake


@pytest.fixture
def reset(monkeypatch):
    mod = _load("reset_lake_under_test", "scripts/reset_lake.py")
    seeded = []
    monkeypatch.setattr(mod, "seed_lake", lambda *a, **k: seeded.append(True) or 4)
    mod.seeded = seeded
    return mod


def test_reset_dry_run_changes_nothing(reset, lake, audit_log, capsys):
    assert reset.main([]) == 0
    assert (lake / "stage2_queue" / "_delta_log").is_dir() and not reset.seeded
    out = capsys.readouterr().out
    assert "stage2_queue" in out and "Dry run only" in out


def test_reset_force_flag_is_gone(reset, lake):
    assert reset.main(["--force"]) == 2
    assert lake.exists()


def test_reset_moves_lake_aside_and_is_restorable(reset, lake, audit_log, monkeypatch):
    monkeypatch.setenv("ALLOW_LAKE_RESET", "1")
    assert reset.main(["--execute", "--i-really-mean-it"]) == 0
    backups = list(lake.parent.glob("lake.bak-*"))
    assert len(backups) == 1 and not lake.exists() and reset.seeded
    assert DeltaTable(str(backups[0] / "stage2_queue")).to_pyarrow_table().num_rows == 5
    done = records(audit_log)[-1]
    assert done["outcome"] == "executed" and done["details"]["backup"] == str(backups[0])
    assert done["details"]["tables"]["stage2_queue"]["rows"] == 5


def test_reset_targets_delta_lake_path_not_constant(reset, lake, monkeypatch):
    from src.core.constants import DELTA_LAKE

    assert reset.resolve_lake_path() == lake != DELTA_LAKE


def test_reset_no_backup_needs_hard_delete_env(reset, lake, audit_log, monkeypatch):
    monkeypatch.setenv("ALLOW_LAKE_RESET", "1")
    assert reset.main(["--execute", "--i-really-mean-it", "--no-backup"]) == 3
    assert lake.exists() and not reset.seeded
    monkeypatch.setenv("DELTA_ALLOW_HARD_DELETE", "1")
    assert reset.main(["--execute", "--i-really-mean-it", "--no-backup"]) == 0
    assert not lake.exists() and not list(lake.parent.glob("lake.bak-*"))


def test_reset_production_refused_without_break_glass(reset, lake, audit_log, monkeypatch):
    monkeypatch.setenv("ALLOW_LAKE_RESET", "1")
    monkeypatch.setenv("ENV", "production")
    assert reset.main(["--execute", "--i-really-mean-it"]) == 3
    assert lake.exists()


# ---------------------------------------------------------------- shell / make


def test_complete_reset_script_defaults_to_dry_run(audit_log):
    env = {**os.environ, "LAKE_AUDIT_LOG": str(audit_log), "PYTHON": sys.executable}
    env.pop("ALLOW_LAKE_RESET", None)
    out = subprocess.run(["bash", "scripts/complete_reset.sh"], cwd=PROJECT, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    assert out.returncode == 0 and "Nothing changed" in out.stdout
    out = subprocess.run(["bash", "scripts/complete_reset.sh", "--execute", "--i-really-mean-it"], cwd=PROJECT, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
    assert out.returncode == 3
    outcomes = [r["outcome"] for r in records(audit_log)]
    assert outcomes == ["dry_run", "refused"]
    assert "volume:delta_data" in records(audit_log)[0]["targets"]


@pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
@pytest.mark.parametrize("target", ["docker-down-clean", "db-reset", "clean-all"])
def test_make_destructive_targets_stop_on_dry_run(audit_log, target):
    env = {**os.environ, "LAKE_AUDIT_LOG": str(audit_log), "GUARD_PY": sys.executable, "COMPOSE": "false"}
    for var in ("ALLOW_LAKE_RESET", "I_REALLY_MEAN_IT"):
        env.pop(var, None)
    out = subprocess.run(["make", target], cwd=PROJECT, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode != 0 and "Dry run: nothing removed" in out.stdout
    assert records(audit_log)[-1]["outcome"] == "dry_run"
