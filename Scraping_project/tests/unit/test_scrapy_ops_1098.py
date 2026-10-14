"""scrapy-ops lake compact|vacuum|doctor and the temp_scripts retirement (#1098)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pyarrow as pa
from deltalake import write_deltalake

from src.ops import scrapy_ops

PROJECT = Path(__file__).resolve().parents[2]
REPO = PROJECT.parent


def _ok_redis(timeout: float = 3.0):
    return True, "redis: ok testhost:6379 db=0"


def _ok_kafka(timeout: float = 5.0):
    return True, "kafka: ok scraped-items"


def _patch_probes(monkeypatch):
    monkeypatch.setattr(scrapy_ops, "ping_redis", _ok_redis)
    monkeypatch.setattr(scrapy_ops, "check_kafka", _ok_kafka)


def test_help_lists_lake_commands(capsys):
    assert scrapy_ops.main(["--help"]) == 0
    assert "lake" in capsys.readouterr().out
    assert scrapy_ops.main(["lake", "--help"]) == 0
    out = capsys.readouterr().out
    for name in ("compact", "vacuum", "doctor"):
        assert name in out


def test_missing_action_is_usage():
    assert scrapy_ops.main([]) == 2


def test_doctor_catches_missing_delta_log(tmp_path, monkeypatch, capsys):
    _patch_probes(monkeypatch)
    table = tmp_path / "stage1_discovery"
    table.mkdir()
    (table / "part-000.parquet").write_bytes(b"not-a-log")
    code = scrapy_ops.main(["lake", "doctor", "--base", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1
    assert "stage1_discovery: missing _delta_log" in out
    assert "redis: ok" in out and "kafka: ok" in out


def test_doctor_catches_missing_lake_path_without_creating_it(tmp_path, monkeypatch, capsys):
    _patch_probes(monkeypatch)
    base = tmp_path / "no-such-lake"
    code = scrapy_ops.main(["lake", "doctor", "--base", str(base)])
    out = capsys.readouterr().out
    assert code == 1
    assert "missing Delta" in out
    assert not base.exists()


def test_doctor_ignores_empty_table_dir(tmp_path, monkeypatch, capsys):
    _patch_probes(monkeypatch)
    (tmp_path / "stage1_discovery").mkdir()
    code = scrapy_ops.main(["lake", "doctor", "--base", str(tmp_path), "--table", "stage1_discovery"])
    out = capsys.readouterr().out
    assert code == 0
    assert "missing _delta_log" not in out
    assert "delta: ok" in out


def test_doctor_accepts_a_real_table_and_rejects_a_broken_log(tmp_path, monkeypatch, capsys):
    _patch_probes(monkeypatch)
    healthy = tmp_path / "healthy"
    write_deltalake(str(healthy), pa.table({"a": [1]}))
    assert scrapy_ops.main(["lake", "doctor", "--base", str(healthy)]) == 0
    assert "delta: ok" in capsys.readouterr().out

    broken = tmp_path / "broken"
    log = broken / "_delta_log"
    log.mkdir(parents=True)
    (log / "00000000000000000000.json").write_text("not-json", encoding="utf-8")
    assert scrapy_ops.main(["lake", "doctor", "--base", str(broken)]) == 1
    assert "unhealthy" in capsys.readouterr().out


def test_doctor_reports_redis_and_kafka_failures(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(scrapy_ops, "ping_redis", lambda timeout=3.0: (False, "redis: FAIL ping testhost:6379 db=0: down"))
    monkeypatch.setattr(
        scrapy_ops, "check_kafka", lambda timeout=5.0: (False, "kafka: FAIL missing topics: scraped-items")
    )
    code = scrapy_ops.main(["lake", "doctor", "--base", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1
    assert "redis: FAIL" in out
    assert "missing topics: scraped-items" in out
    assert "delta: ok" in out


def test_redis_ping_uses_host_port_password_db(monkeypatch):
    captured = {}

    class FakeRedis:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def ping(self):
            return True

        def close(self):
            return None

    monkeypatch.setenv("REDIS_HOST", "redis.internal")
    monkeypatch.setenv("REDIS_PORT", "6380")
    monkeypatch.setenv("REDIS_PASSWORD", "secret")
    monkeypatch.setenv("REDIS_DB", "2")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr("redis.Redis", FakeRedis)
    ok, line = scrapy_ops.ping_redis(1.0)
    assert ok is True
    assert captured["host"] == "redis.internal"
    assert captured["port"] == 6380
    assert captured["password"] == "secret"
    assert captured["db"] == 2
    assert "url" not in captured
    assert "secret" not in line
    assert "redis.internal:6380 db=2" in line


def test_compact_fails_on_missing_log_and_compacts_a_real_log(tmp_path, monkeypatch, capsys):
    broken = tmp_path / "lake"
    table = broken / "stage1_discovery"
    table.mkdir(parents=True)
    (table / "part.parquet").write_bytes(b"x")
    code = scrapy_ops.main(["lake", "compact", "--base", str(broken), "--table", "stage1_discovery"])
    assert code == 1
    assert "missing _delta_log" in capsys.readouterr().out

    calls = []
    from src.lakehouse.lakehouse_manager import LakehouseManager

    monkeypatch.setattr(LakehouseManager, "_optimize_table", lambda self, name: calls.append(name))
    good = tmp_path / "good" / "stage1_discovery"
    good.parent.mkdir()
    write_deltalake(str(good), pa.table({"a": [1]}))
    code = scrapy_ops.main(["lake", "compact", "--base", str(good.parent), "--table", "stage1_discovery"])
    assert code == 0
    assert calls == ["stage1_discovery"]
    assert "stage1_discovery: compacted" in capsys.readouterr().out


def test_vacuum_preview_does_not_apply_and_apply_does(tmp_path, monkeypatch, capsys):
    from src.lakehouse.lakehouse_manager import LakehouseManager

    base = tmp_path / "lake"
    path = base / "stage1_discovery"
    path.mkdir(parents=True)
    write_deltalake(str(path), pa.table({"a": [1]}))
    preview = {"table": "stage1_discovery", "retention_hours": 168, "files": ["old.parquet"], "bytes": 4}
    monkeypatch.setattr(LakehouseManager, "vacuum_preview", lambda self, name, hours: preview)
    applied = []
    monkeypatch.setattr(
        LakehouseManager,
        "_vacuum_table",
        lambda self, name, hours, enforce_retention_duration=True: applied.append(
            (name, hours, enforce_retention_duration)
        ),
    )
    code = scrapy_ops.main(["lake", "vacuum", "--base", str(base), "--table", "stage1_discovery"])
    out = capsys.readouterr().out
    assert code == 0 and applied == []
    assert "pass --apply" in out

    code = scrapy_ops.main(
        [
            "lake", "vacuum", "--base", str(base), "--table", "stage1_discovery",
            "--retention-hours", "168", "--apply",
        ]
    )
    assert code == 0
    assert applied == [("stage1_discovery", 168, True)]


def test_lake_table_names_match_the_manager(tmp_path):
    from src.lakehouse.lakehouse_manager import LakehouseManager

    manager = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    assert set(scrapy_ops.LAKE_TABLES) == set(manager.tables)


def test_wrapper_help():
    script = PROJECT / "scripts" / "ops" / "scrapy-ops"
    proc = subprocess.run([sys.executable, str(script), "lake", "--help"], capture_output=True, text=True)
    assert proc.returncode == 0
    assert "doctor" in proc.stdout


def test_console_script_points_at_scrapy_ops():
    text = (PROJECT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'scrapy-ops = "src.ops.scrapy_ops:cli"' in text


def test_temp_scripts_stay_empty_and_ci_rejects_them():
    found = []
    for rel in ("temp_scripts", "Scraping_project/temp_scripts"):
        root = REPO / rel
        if root.exists():
            found.extend(str(p.relative_to(REPO)) for p in root.rglob("*") if p.is_file())
    assert found == []
    workflow = (REPO / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
    assert "temp_scripts" in workflow


def test_refuses_path_like_table_names(tmp_path, capsys):
    code = scrapy_ops.main(["lake", "doctor", "--base", str(tmp_path), "--table", "../etc"])
    assert code == 2
    assert "refusing table name" in capsys.readouterr().out
