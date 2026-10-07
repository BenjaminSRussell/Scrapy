"""#225: failed async writes retry then spill durably; #167: bounded queue put."""

import json
import threading
import time

import pytest

from src.lakehouse import lakehouse_manager as lm
from src.lakehouse.lakehouse_manager import LakehouseManager


@pytest.fixture
def mgr(tmp_path):
    m = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    m.write_retry_backoff = 0.0
    yield m
    m.shutdown_event.set()


def _counter(metric, **labels):
    return metric.labels(**labels)._value.get() if metric is not None else None


def test_transient_failure_is_retried_then_written(mgr, monkeypatch):
    calls = {"n": 0}
    real = mgr._write_sync

    def flaky(table, data, mode):
        calls["n"] += 1
        return False if calls["n"] < 3 else real(table, data, mode)

    monkeypatch.setattr(mgr, "_write_sync", flaky)
    assert mgr._write_with_retry("t225", [{"url": "a", "n": 1}], "append") is True
    assert calls["n"] == 3
    assert not mgr.spill_path.exists()
    assert [r["url"] for r in mgr.read("t225")] == ["a"]


def test_exhausted_retries_spill_durably_and_replay(mgr, monkeypatch):
    monkeypatch.setattr(mgr, "_write_sync", lambda *a, **k: False)
    before = _counter(lm.DELTA_WRITE_FAILURES, table="t225", outcome="spilled")
    rows = [{"url": f"u{i}", "n": i} for i in range(3)]
    assert mgr._write_with_retry("t225", rows, "append") is False

    files = list((mgr.spill_path / "t225").glob("*.jsonl"))
    assert len(files) == 1 and not list(mgr.spill_path.rglob("*.tmp"))
    lines = files[0].read_text().splitlines()
    assert json.loads(lines[0])["_spill_meta"]["table"] == "t225"
    assert [json.loads(line)["url"] for line in lines[1:]] == ["u0", "u1", "u2"]
    if before is not None:
        assert _counter(lm.DELTA_WRITE_FAILURES, table="t225", outcome="spilled") == before + 1

    monkeypatch.undo()  # storage healthy again
    result = mgr.replay_spilled_writes()
    assert result == {"files": 1, "rows": 3, "failed": 0}
    assert not list((mgr.spill_path / "t225").glob("*.jsonl"))
    assert sorted(r["url"] for r in mgr.read("t225")) == ["u0", "u1", "u2"]


def test_queue_worker_acks_only_after_write_or_spill(mgr, monkeypatch):
    monkeypatch.setattr(mgr, "_write_sync", lambda *a, **k: False)
    mgr.write("t225", [{"url": "q"}], async_write=True)
    worker = threading.Thread(target=mgr._process_queue, daemon=True)
    worker.start()
    mgr.write_queue.join()  # returns only once task_done ran
    assert list((mgr.spill_path / "t225").glob("*.jsonl")), "acked batch must be on disk"
    mgr.shutdown_event.set()
    worker.join(timeout=5)


def test_full_queue_backpressures_within_timeout(tmp_path):
    m = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    m.write_queue.maxsize = 2  # no worker is draining it
    m.queue_put_timeout = 0.2
    assert m.write("t167", [{"url": "1"}]) is True
    assert m.write("t167", [{"url": "2"}]) is True
    before = _counter(lm.DELTA_WRITE_QUEUE_FULL, table="t167")
    start = time.monotonic()
    assert m.write("t167", [{"url": "3"}]) is False  # used to block forever
    assert time.monotonic() - start < 2.0
    spilled = list((m.spill_path / "t167").glob("*.jsonl"))
    assert len(spilled) == 1 and '"url": "3"' in spilled[0].read_text()
    if before is not None:
        assert _counter(lm.DELTA_WRITE_QUEUE_FULL, table="t167") == before + 1


def test_sync_write_reports_failure(mgr, monkeypatch):
    # Any exception inside the write (here an unreadable table schema) used to
    # be logged and swallowed with no signal to the caller.
    def broken(_path):
        raise RuntimeError("corrupt _delta_log")

    monkeypatch.setattr(mgr, "_table_schema", broken)
    assert mgr.write("t225", [{"url": "x"}], async_write=False) is False
