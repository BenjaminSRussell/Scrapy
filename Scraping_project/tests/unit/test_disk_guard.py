"""#561: free-space and inode watermarks for the lake volume.

Volume numbers are faked through ``os.statvfs``; the host disk is never filled.
"""

import logging
from types import SimpleNamespace

import pytest

from src.lakehouse import lakehouse_manager as lm
from src.lakehouse.lakehouse_manager import SPILL_DIR_NAME, LakehouseManager
from src.utils import disk_guard, probe

TABLE = "stage2_errors"


def _volume(monkeypatch, *, free_blocks, blocks=1000, free_inodes=1000, inodes=1000):
    """Fake the volume holding any path: ``free_blocks`` of ``blocks`` free for non-root."""
    stat = SimpleNamespace(
        f_bavail=free_blocks, f_blocks=blocks, f_frsize=4096,
        f_favail=free_inodes, f_files=inodes,
    )
    monkeypatch.setattr(disk_guard.os, "statvfs", lambda _path: stat)


def test_healthy_volume_has_no_shortfall(tmp_path, monkeypatch):
    _volume(monkeypatch, free_blocks=500)
    status = disk_guard.check_volume(tmp_path, 5, 5)
    assert status.shortfall is None
    assert status.free_percent == pytest.approx(50.0)
    assert status.free_inodes_percent == pytest.approx(100.0)


def test_low_free_space_is_a_shortfall(tmp_path, monkeypatch):
    _volume(monkeypatch, free_blocks=40)
    status = disk_guard.check_volume(tmp_path, 5, 5)
    assert status.shortfall and "free space 4.0%" in status.shortfall


def test_low_inodes_is_a_shortfall_even_with_free_bytes(tmp_path, monkeypatch):
    _volume(monkeypatch, free_blocks=900, free_inodes=10)
    status = disk_guard.check_volume(tmp_path, 5, 5)
    assert status.shortfall and "free inodes 1.0%" in status.shortfall


def test_watermark_is_inclusive_of_the_threshold(tmp_path, monkeypatch):
    _volume(monkeypatch, free_blocks=50)  # exactly 5%: not below the watermark
    assert disk_guard.check_volume(tmp_path, 5, 5).shortfall is None


def test_unreadable_volume_fails_closed(tmp_path, monkeypatch):
    def boom(_path):
        raise OSError(5, "I/O error")

    monkeypatch.setattr(disk_guard.os, "statvfs", boom)
    status = disk_guard.check_volume(tmp_path, 5, 5)
    assert status.shortfall and "cannot stat volume" in status.shortfall


def test_missing_path_is_checked_at_nearest_existing_parent(tmp_path, monkeypatch):
    seen = []
    stat = SimpleNamespace(f_bavail=500, f_blocks=1000, f_frsize=1, f_favail=500, f_files=1000)
    monkeypatch.setattr(disk_guard.os, "statvfs", lambda p: seen.append(p) or stat)
    disk_guard.check_volume(tmp_path / "not" / "yet" / "created", 5, 5)
    assert seen == [tmp_path]


def test_ready_disk_reports_the_shortfall(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.utils.disk_guard.check_volume",
        lambda *a, **k: disk_guard.VolumeStatus(1.0, 50.0, "free space 1.0% below watermark 5%"),
    )
    ok, why = probe.disk_ready(str(tmp_path))
    assert ok is False and why == "free space 1.0% below watermark 5%"
    assert probe.main(["ready-disk"]) == 1


def test_ready_disk_passes_on_healthy_volume(tmp_path, monkeypatch):
    _volume(monkeypatch, free_blocks=500)
    assert probe.disk_ready(str(tmp_path)) == (True, "ok")


@pytest.fixture
def low_disk(monkeypatch):
    """Every LakehouseManager volume check reports a full disk."""
    monkeypatch.setattr(
        lm,
        "check_volume",
        lambda *a, **k: disk_guard.VolumeStatus(1.0, 50.0, "free space 1.0% below watermark 5%"),
    )


@pytest.fixture
def mgr(tmp_path):
    m = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    m.write_retries = 3
    m.write_retry_backoff = 0.0
    yield m
    m.shutdown_event.set()


def test_sync_write_refused_below_watermark_without_touching_delta(mgr, low_disk, monkeypatch, caplog):
    calls = []
    monkeypatch.setattr("deltalake.write_deltalake", lambda *a, **k: calls.append(a))
    with caplog.at_level(logging.ERROR):
        assert mgr._write_sync(TABLE, [{"n": 1}]) is False
    assert calls == []
    assert f"[DISK] refusing 1 rows for {TABLE}" in caplog.text
    assert not (mgr.base_path / TABLE / "_delta_log").exists()


def test_async_write_refused_before_queueing(mgr, low_disk):
    assert mgr.write(TABLE, [{"n": 1}], async_write=True) is False
    assert mgr.write_queue.qsize() == 0


def test_refused_batch_is_neither_retried_nor_spilled(mgr, low_disk, monkeypatch):
    attempts = []
    monkeypatch.setattr(mgr, "_write_sync", lambda *a, **k: attempts.append(a) or False)
    assert mgr._write_with_retry(TABLE, [{"n": 1}], "append") is False
    assert attempts == []
    assert not (mgr.base_path / SPILL_DIR_NAME).exists()


def test_writes_resume_once_the_volume_recovers(mgr, monkeypatch):
    healthy = disk_guard.VolumeStatus(50.0, 50.0, None)
    monkeypatch.setattr(lm, "check_volume", lambda *a, **k: healthy)
    assert mgr._write_sync(TABLE, [{"url": "https://www.uconn.edu/p/1", "error": "e", "n": 1}]) is True
