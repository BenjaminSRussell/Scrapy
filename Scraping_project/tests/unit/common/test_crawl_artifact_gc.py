"""TTL GC for crawl artifacts (#1102)."""
from __future__ import annotations

import time
from pathlib import Path

from src.common.crawl_artifact_gc import apply_gc, plan_gc


def test_dry_run_does_not_delete(tmp_path: Path):
    logs = tmp_path / "data" / "logs"
    logs.mkdir(parents=True)
    old = logs / "old.log"
    old.write_text("x" * 100)
    # age the file
    old_mtime = time.time() - 10 * 86400
    import os
    os.utime(old, (old_mtime, old_mtime))
    fresh = logs / "fresh.log"
    fresh.write_text("y" * 50)

    report = plan_gc(base_dir=tmp_path, ttl_days=7, roots=["data/logs"])
    assert report.dry_run is True
    assert report.total_files == 1
    assert report.total_bytes == 100
    assert old.exists() and fresh.exists()


def test_apply_deletes_only_expired(tmp_path: Path):
    logs = tmp_path / "data" / "logs"
    logs.mkdir(parents=True)
    old = logs / "old.log"
    old.write_text("x" * 100)
    import os
    old_mtime = time.time() - 10 * 86400
    os.utime(old, (old_mtime, old_mtime))
    fresh = logs / "fresh.log"
    fresh.write_text("y" * 50)

    report = plan_gc(base_dir=tmp_path, ttl_days=7, roots=["data/logs"])
    applied = apply_gc(report)
    assert applied.dry_run is False
    assert applied.deleted_files == 1
    assert not old.exists()
    assert fresh.exists()
