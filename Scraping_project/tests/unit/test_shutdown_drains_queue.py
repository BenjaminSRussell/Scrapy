"""#166: SIGTERM/shutdown drains the async write queue; nothing queued is lost.

A hard kill (SIGKILL/OOM) still loses batches that only exist in process memory;
that window is what the DeltaWriteQueueBacklog alert watches.
"""

import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
from deltalake import DeltaTable

from src.lakehouse.lakehouse_manager import DeltaLakeManager

TABLE = "drain_table"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def no_signals(monkeypatch):
    monkeypatch.setattr(signal, "signal", lambda *a, **k: None)


def _slow_writes(manager, monkeypatch, delay=0.02, gate=None):
    original = manager._write_sync_locked

    def slow(*args, **kwargs):
        if gate is not None:
            gate.wait(10)
        time.sleep(delay)
        return original(*args, **kwargs)

    monkeypatch.setattr(manager, "_write_sync_locked", slow)


def _rows(path):
    return DeltaTable(str(path)).to_pyarrow_table().num_rows if (path / "_delta_log").exists() else 0


def test_shutdown_writes_every_queued_batch(tmp_path, monkeypatch, no_signals):
    mgr = DeltaLakeManager(base_path=str(tmp_path / "lake"), start_workers=True)
    _slow_writes(mgr, monkeypatch)
    for i in range(20):
        assert mgr.write(TABLE, [{"i": i}], async_write=True)
    mgr.shutdown(timeout=15)
    assert _rows(mgr.get_table_path(TABLE)) == 20
    assert not list(mgr.spill_path.rglob("*.jsonl"))


def test_shutdown_timeout_spills_instead_of_dropping(tmp_path, monkeypatch, no_signals):
    mgr = DeltaLakeManager(base_path=str(tmp_path / "lake"), start_workers=True)
    gate = threading.Event()
    _slow_writes(mgr, monkeypatch, delay=0, gate=gate)  # worker stuck on batch 0
    for i in range(10):
        mgr.write(TABLE, [{"i": i}], async_write=True)
    time.sleep(0.2)

    started = time.time()
    mgr.shutdown(timeout=1)
    assert time.time() - started < 8, "shutdown must not hang"
    spilled = list(mgr.spill_path.rglob("*.jsonl"))
    assert len(spilled) == 9

    gate.set()
    mgr.worker_thread.join(5)
    assert not mgr.worker_thread.is_alive()
    mgr.replay_spilled_writes()
    assert _rows(mgr.get_table_path(TABLE)) == 10


def test_checkpoint_is_bounded_without_a_worker(tmp_path):
    mgr = DeltaLakeManager(base_path=str(tmp_path / "lake"), start_workers=False)
    mgr.write_queue.put((TABLE, [{"i": 1}], "append"))
    started = time.time()
    mgr.checkpoint(timeout=1)  # Queue.join() used to block forever here
    assert time.time() - started < 5


def test_async_write_after_shutdown_is_written_synchronously(tmp_path, no_signals):
    mgr = DeltaLakeManager(base_path=str(tmp_path / "lake"), start_workers=True)
    mgr.shutdown(timeout=5)
    assert mgr.write(TABLE, [{"i": 1}], async_write=True)
    assert mgr.write_queue.qsize() == 0
    assert _rows(mgr.get_table_path(TABLE)) == 1


CHILD = textwrap.dedent(
    """
    import sys, time
    sys.path.insert(0, {root!r})
    from src.lakehouse.lakehouse_manager import DeltaLakeManager

    mgr = DeltaLakeManager(base_path={base!r}, start_workers=True)
    original = mgr._write_sync_locked
    def slow(*a, **k):
        time.sleep(0.1)
        return original(*a, **k)
    mgr._write_sync_locked = slow
    for i in range(25):
        mgr.write("{table}", [{{"i": i}}], async_write=True)
    print("ready", flush=True)
    time.sleep(60)
    """
)


def test_sigterm_with_backlog_loses_nothing(tmp_path):
    base = tmp_path / "lake"
    script = tmp_path / "child.py"
    script.write_text(CHILD.format(root=str(ROOT), base=str(base), table=TABLE))
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True, cwd=ROOT, env=env)
    try:
        assert proc.stdout.readline().strip() == "ready"
        proc.send_signal(signal.SIGTERM)  # backlog of ~25 slow batches still queued
        assert proc.wait(timeout=60) == 0
    finally:
        if proc.poll() is None:
            proc.kill()

    written = _rows(base / TABLE)
    spilled = sum(
        len(p.read_text().splitlines()) - 1 for p in (base / "_write_spill").rglob("*.jsonl")
    ) if (base / "_write_spill").exists() else 0
    assert written + spilled == 25
