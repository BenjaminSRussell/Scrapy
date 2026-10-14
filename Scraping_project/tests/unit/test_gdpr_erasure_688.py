"""GDPR erasure stub: dry-run, execute, tombstone TTL, audit (#688)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.lakehouse.gdpr_erasure import LEGAL_NOTE, erase_url_hashes, validate_url_hashes
from src.lakehouse.lakehouse_manager import LakehouseManager

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
HASH_A = "a" * 16
HASH_B = "b" * 16
ROW = {
    "url": "https://example.edu/page",
    "discovered_at": "2026-01-01T00:00:00+00:00",
    "processed_at": "2026-01-01T00:00:00+00:00",
}


@pytest.fixture
def manager(tmp_path):
    return LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)


def _put(manager, table, digest, **extra):
    row = {**ROW, "url_hash": digest, **extra}
    assert manager.write(table, [row], async_write=False) is True


def _hashes(manager, table):
    if not manager.table_exists(table):
        return []
    return [row.get("url_hash") for row in manager.read(table)]


def test_invalid_url_hash_is_rejected(manager):
    with pytest.raises(ValueError):
        validate_url_hashes(["hash;drop table"])
    with pytest.raises(ValueError):
        erase_url_hashes(manager, ["https://example.edu/page"], execute=False)


def test_dry_run_reports_matches_and_writes_nothing(manager):
    _put(manager, "stage1_discovery", HASH_A)
    _put(manager, "stage2_page_analysis", HASH_A)
    _put(manager, "stage2_page_analysis", HASH_B)

    result = erase_url_hashes(manager, [HASH_A], execute=False, actor="tester", reason="drill")

    assert result["dry_run"] is True
    assert result["legal_review_required"] is True
    assert LEGAL_NOTE in result["note"]
    assert result["matched"]["stage1_discovery"] == 1
    assert result["matched"]["stage2_page_analysis"] == 1
    assert result["deleted"] == {}
    assert result["tombstones_written"] == 0
    assert result["audit_path"] is None
    assert not (manager.base_path / "_audit" / "gdpr_erasure.jsonl").exists()
    assert not (manager.base_path / "gdpr_tombstones" / "_delta_log").exists()
    assert _hashes(manager, "stage1_discovery") == [HASH_A]
    assert set(_hashes(manager, "stage2_page_analysis")) == {HASH_A, HASH_B}


def test_execute_deletes_writes_tombstone_and_audit(manager):
    _put(manager, "stage1_discovery", HASH_A)
    _put(manager, "stage3_summaries", HASH_A)
    _put(manager, "stage3_summaries", HASH_B)

    result = erase_url_hashes(
        manager, [HASH_A, HASH_A], execute=True, ttl_seconds=3600, actor="counsel", reason="request"
    )

    assert result["dry_run"] is False
    assert result["errors"] == {}
    assert result["deleted"]["stage1_discovery"] == 1
    assert result["deleted"]["stage3_summaries"] == 1
    assert result["tombstones_written"] == 1
    assert _hashes(manager, "stage1_discovery") == []
    assert _hashes(manager, "stage3_summaries") == [HASH_B]

    audit = Path(result["audit_path"])
    record = json.loads(audit.read_text(encoding="utf-8").strip())
    assert record["action"] == "erase"
    assert record["url_hashes"] == [HASH_A]
    assert record["actor"] == "counsel"
    assert record["reason"] == "request"
    assert record["legal_review_required"] is True
    assert record["deleted"]["stage1_discovery"] == 1
    assert "https://" not in audit.read_text(encoding="utf-8")

    from deltalake import DeltaTable

    tombstones = DeltaTable(str(manager.base_path / "gdpr_tombstones")).to_pyarrow_table().to_pylist()
    assert len(tombstones) == 1
    assert tombstones[0]["url_hash"] == HASH_A
    assert tombstones[0]["legal_review_required"] == "true"


def test_tombstone_blocks_write_and_merge_until_ttl(manager):
    _put(manager, "stage2_page_analysis", HASH_B)
    erase_url_hashes(manager, [HASH_A], execute=True, ttl_seconds=86400, now=datetime.now(UTC))

    assert manager.write("stage1_discovery", [{**ROW, "url_hash": HASH_A}], async_write=False) is True
    assert _hashes(manager, "stage1_discovery") == []

    merged = manager.merge_into(
        "stage2_page_analysis",
        [{**ROW, "url_hash": HASH_A}],
        merge_key="url_hash",
        update_columns=["url", "processed_at"],
    )
    assert merged == 0
    assert HASH_A not in _hashes(manager, "stage2_page_analysis")
    assert HASH_B in _hashes(manager, "stage2_page_analysis")

    assert manager.write("stage1_discovery", [{**ROW, "url_hash": HASH_B}], async_write=False) is True
    assert _hashes(manager, "stage1_discovery") == [HASH_B]


def test_expired_tombstone_allows_reingest(manager):
    past = datetime(2020, 1, 1, tzinfo=UTC)
    erase_url_hashes(manager, [HASH_A], execute=True, ttl_seconds=60, now=past)
    assert manager.write("stage1_discovery", [{**ROW, "url_hash": HASH_A}], async_write=False) is True
    assert _hashes(manager, "stage1_discovery") == [HASH_A]


def test_later_tombstone_keeps_hash_blocked(manager):
    soon = datetime.now(UTC) + timedelta(days=1)
    erase_url_hashes(manager, [HASH_A], execute=True, ttl_seconds=86400 * 10, now=datetime.now(UTC))
    erase_url_hashes(manager, [HASH_A], execute=True, ttl_seconds=60, now=soon)
    assert manager.write("stage2_queue", [{**ROW, "url_hash": HASH_A, "status": "pending"}], async_write=False) is True
    assert _hashes(manager, "stage2_queue") == []


def test_cli_dry_run_and_execute(manager):
    _put(manager, "stage1_discovery", HASH_A)
    script = ROOT / "scripts" / "gdpr_erase.py"
    base = ["--url-hash", HASH_A, "--base-path", str(manager.base_path), "--ttl-days", "1"]

    help_run = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert help_run.returncode == 0
    assert "Legal review is still required" in help_run.stdout

    both = subprocess.run(
        [sys.executable, str(script), *base, "--dry-run", "--execute"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert both.returncode == 2

    dry = subprocess.run(
        [sys.executable, str(script), *base],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert dry.returncode == 0, dry.stderr
    assert "DRY-RUN" in dry.stdout
    assert "Legal review is still required" in dry.stdout
    assert "matched stage1_discovery: 1" in dry.stdout
    assert _hashes(manager, "stage1_discovery") == [HASH_A]

    done = subprocess.run(
        [sys.executable, str(script), *base, "--execute", "--actor", "cli", "--reason", "ticket"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert done.returncode == 0, done.stderr
    assert "EXECUTED" in done.stdout
    assert "tombstones_written=1" in done.stdout
    assert _hashes(manager, "stage1_discovery") == []
    audit = (manager.base_path / "_audit" / "gdpr_erasure.jsonl").read_text(encoding="utf-8")
    assert "ticket" in audit
    assert HASH_A in audit
