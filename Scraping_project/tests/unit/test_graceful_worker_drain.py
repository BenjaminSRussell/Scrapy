"""SIGTERM/SIGINT drain for continuous stage workers (#183, #185, #325)."""

from __future__ import annotations

import asyncio
import gc
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from src.utils import graceful_shutdown as gs
from src.utils.graceful_shutdown import GracefulShutdown, run_drain_loop

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _fresh_shutdown():
    gs.reset_for_tests()
    yield
    gs.reset_for_tests()


# ------------------------------------------------------------ helper unit tests


def test_sleep_returns_early_once_shutdown_is_requested():
    sd = GracefulShutdown()

    async def go():
        asyncio.get_running_loop().call_later(0.1, sd.request, "test")
        start = time.monotonic()
        stopped = await sd.sleep(30)
        return stopped, time.monotonic() - start

    stopped, elapsed = asyncio.run(go())
    assert stopped is True
    assert elapsed < 2
    assert asyncio.run(GracefulShutdown().sleep(0.05)) is False


def test_signal_without_a_drain_loop_flushes_and_exits_zero():
    sd = GracefulShutdown()
    ran = []
    sd.add_cleanup(lambda: ran.append("flush"))
    with pytest.raises(SystemExit) as exc:
        sd.handle_signal(signal.SIGTERM)
    assert exc.value.code == 0
    assert ran == ["flush"]
    assert sd.reason == "SIGTERM"


def test_signal_during_a_drain_loop_only_sets_the_flag_then_second_signal_forces():
    forced = []
    sd = GracefulShutdown(force_exit=forced.append)
    sd.timeout = 60
    ran = []
    sd.add_cleanup(lambda: ran.append("flush"))
    sd.begin_drain()

    sd.handle_signal(signal.SIGTERM)  # no exception: the loop drains cooperatively
    assert sd.requested and ran == [] and forced == []

    with pytest.raises(SystemExit) as exc:
        sd.handle_signal(signal.SIGINT)
    assert exc.value.code == 128 + signal.SIGINT
    sd.end_drain()  # cancels the overrun timer


def test_drain_overrun_is_force_exited_with_status_1():
    forced = []
    sd = GracefulShutdown(force_exit=forced.append)
    sd.timeout = 0.05
    sd.begin_drain()
    sd.handle_signal(signal.SIGTERM)
    deadline = time.time() + 5
    while not forced and time.time() < deadline:
        time.sleep(0.01)
    assert forced == [1]


def test_finished_drain_cancels_the_overrun_timer():
    forced = []
    sd = GracefulShutdown(force_exit=forced.append)
    sd.timeout = 0.2
    sd.begin_drain()
    sd.handle_signal(signal.SIGTERM)
    sd.end_drain()
    time.sleep(0.4)
    assert forced == []


def test_cleanups_run_once_newest_first_and_survive_failures():
    sd = GracefulShutdown()
    order = []

    def boom():
        raise RuntimeError("x")

    sd.add_cleanup(lambda: order.append("first"))
    sd.add_cleanup(boom)
    sd.add_cleanup(lambda: order.append("last"))
    sd.run_cleanups()
    sd.run_cleanups()
    assert order == ["last", "first"]


def test_bound_method_cleanups_do_not_keep_instances_alive():
    sd = GracefulShutdown()

    class Manager:
        calls = 0

        def close(self):
            Manager.calls += 1

    m = Manager()
    sd.add_cleanup(m.close)
    del m
    gc.collect()
    sd.run_cleanups()
    assert Manager.calls == 0


def test_install_keeps_foreign_handlers_unless_overriding():
    def foreign(signum, frame):
        pass

    previous = signal.signal(signal.SIGTERM, foreign)
    try:
        sd = gs.get_shutdown()
        assert gs.install_signal_handlers(sd, (signal.SIGTERM,)) is False
        assert signal.getsignal(signal.SIGTERM) is foreign  # e.g. Scrapy's graceful stop
        assert gs.install_signal_handlers(sd, (signal.SIGTERM,), override=True) is True
        assert signal.getsignal(signal.SIGTERM) == sd.handle_signal
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_shutdown_timeout_env(monkeypatch):
    monkeypatch.delenv("WORKER_SHUTDOWN_TIMEOUT", raising=False)
    assert gs.shutdown_timeout_seconds() == 100.0
    monkeypatch.setenv("WORKER_SHUTDOWN_TIMEOUT", "30")
    assert gs.shutdown_timeout_seconds() == 30.0
    for bad in ("abc", "-5", "0", " "):
        monkeypatch.setenv("WORKER_SHUTDOWN_TIMEOUT", bad)
        assert gs.shutdown_timeout_seconds() == 100.0


# ------------------------------------------------------------ the drain loop


def test_drain_loop_stops_starting_runs_after_shutdown_and_cuts_idle_short():
    sd = GracefulShutdown()
    runs = []

    async def run_once():
        runs.append(len(runs))
        if len(runs) == 2:
            sd.request("SIGTERM")

    start = time.monotonic()
    asyncio.run(run_drain_loop("stageX", run_once, idle_seconds=0.01, error_seconds=0.01, shutdown=sd))
    assert runs == [0, 1]
    assert not sd.draining

    # Signal while idling: the 30 s idle sleep returns promptly.
    sd2 = GracefulShutdown()

    async def run_and_signal_later():
        asyncio.get_running_loop().call_later(0.1, sd2.request, "SIGTERM")

    start = time.monotonic()
    asyncio.run(run_drain_loop("stageX", run_and_signal_later, idle_seconds=30, error_seconds=30, shutdown=sd2))
    assert time.monotonic() - start < 3


def test_drain_loop_backs_off_on_errors_and_still_stops():
    sd = GracefulShutdown()
    attempts = []

    async def flaky():
        attempts.append(1)
        if len(attempts) >= 3:
            sd.request("SIGTERM")
        raise RuntimeError("delta unavailable")

    asyncio.run(run_drain_loop("stageX", flaky, idle_seconds=0.01, error_seconds=0.01, shutdown=sd))
    assert len(attempts) == 3


# --------------------------------------------------- stage batch boundaries


def test_stage2_stops_scheduling_batches_and_acks_the_one_in_flight(monkeypatch):
    from tests.unit.stage2.test_stage2_batch_paths import _FakeDelta, _queue, _wire, _worker

    delta = _FakeDelta(_queue(*[f"u{i}" for i in range(6)]))
    worker = _worker(delta, batch_size=2)
    calls, acks = _wire(monkeypatch, worker, {f"u{i}": {"has_error": False, "is_low_quality": False} for i in range(6)})

    original = worker._analyze_url

    async def analyze_then_sigterm(record):
        result = await original(record)
        gs.get_shutdown().request("SIGTERM")  # arrives mid-batch 1
        return result

    monkeypatch.setattr(worker, "_analyze_url", analyze_then_sigterm)
    counts = asyncio.run(worker._run_traced())

    assert calls == ["u0", "u1"]  # batch 1 finished, batches 2-3 never scheduled
    assert acks == [("u0", "completed"), ("u1", "completed")]  # flushed + acked
    assert counts["analyzed"] == 2
    assert [t for t, _ in delta.merges] == ["stage2_page_analysis"]


def test_stage3_finishes_the_batch_in_flight_then_stops(monkeypatch):
    from src.core.constants import TABLE_STAGE3_SUMMARIES
    from tests.unit.stage3.test_stage3_summary_table import FakeDelta, _worker

    docs = [
        {"url_hash": f"h{i}", "url": f"https://x/{i}", "text_content": "word " * 200, "is_low_quality": False}
        for i in range(30)
    ]
    delta = FakeDelta({"stage2_page_analysis": docs})
    w = _worker(delta)
    summarized = []

    async def fake_dedupe(batch):
        return batch

    async def fake_summarize(doc):
        summarized.append(doc["url_hash"])
        gs.get_shutdown().request("SIGTERM")
        return {"url_hash": doc["url_hash"], "summary": "s"}

    monkeypatch.setattr(w, "_deduplicate_documents", fake_dedupe)
    monkeypatch.setattr(w, "_summarize_document", fake_summarize)
    asyncio.run(w._run_traced())

    assert len(summarized) == 10  # one batch of batch_size=10
    assert delta.writes == [TABLE_STAGE3_SUMMARIES]
    assert len(delta.tables[TABLE_STAGE3_SUMMARIES]) == 10


def test_stage4_saves_and_acks_what_it_finished_before_sigterm(tmp_path):
    from src.stage4.stage4_worker import SUMMARY_TABLE
    from tests.unit.test_stage4_queue_input import FakeProcessor, _queue, _statuses, _worker

    class SignallingProcessor(FakeProcessor):
        def process_large_document(self, url, text):
            gs.get_shutdown().request("SIGTERM")
            return super().process_large_document(url, text)

    proc = SignallingProcessor(texts={u: "x" * 100 for u in "abc"}, summaries={u: f"sum-{u}" for u in "abc"})
    w = _worker(tmp_path, proc)
    for u in "abc":
        _queue(w.delta, u)

    assert asyncio.run(w._run_traced()) == 1
    statuses = _statuses(w.delta)
    done = [u for u, s in statuses.items() if s == "completed"]
    assert len(done) == 1 and sorted(s for s in statuses.values()).count("pending") == 2
    assert [r["url"] for r in w.delta.read(SUMMARY_TABLE)] == done


# ------------------------------------------------------- real process + signal

CHILD = textwrap.dedent(
    """
    import asyncio, sys, time
    sys.path.insert(0, {root!r})
    from src.utils.graceful_shutdown import run_drain_loop, shutdown_requested

    batches = []

    async def run_once():
        for i in range(1000):
            if i == 1:
                print("ready", flush=True)
            await asyncio.sleep(0.2)          # one batch of work
            batches.append(i)
            print(f"flushed {{i}}", flush=True)  # write + ack
            if shutdown_requested():
                return

    asyncio.run(run_drain_loop("stage2", run_once, idle_seconds=30, error_seconds=10))
    print(f"exit after {{len(batches)}} batches", flush=True)
    """
)


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_worker_process_drains_on_signal_and_exits_zero(tmp_path, signum):
    script = tmp_path / "worker.py"
    script.write_text(CHILD.format(root=str(ROOT)))
    env = {**os.environ, "PYTHONPATH": str(ROOT), "WORKER_SHUTDOWN_TIMEOUT": "20"}
    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True, cwd=ROOT, env=env)
    try:
        lines = []
        while True:
            line = proc.stdout.readline()
            assert line, "worker exited before it was ready"
            lines.append(line.strip())
            if line.strip() == "ready":
                break
        start = time.monotonic()
        proc.send_signal(signum)
        out, _ = proc.communicate(timeout=20)
        assert proc.returncode == 0
        assert time.monotonic() - start < 5  # batch boundary, not the 30 s idle
    finally:
        if proc.poll() is None:
            proc.kill()
    lines += out.splitlines()
    text = "\n".join(lines)
    assert "exit after" in text
    n = int(out.strip().splitlines()[-1].split()[2])
    assert f"flushed {n - 1}" in text  # the batch in flight was flushed before exit
    assert n <= 3  # and no new batches were started after the signal


def test_lakehouse_manager_uses_the_shared_handler(monkeypatch, tmp_path):
    from src.lakehouse.lakehouse_manager import DeltaLakeManager

    installed = []
    monkeypatch.setattr(gs, "install_signal_handlers", lambda *a, **k: installed.append(1) or True)
    mgr = DeltaLakeManager(base_path=str(tmp_path / "lake"), start_workers=True)
    try:
        assert installed == [1]
        names = [name for name, _ in gs.get_shutdown()._cleanups]
        assert "lakehouse write queue" in names
    finally:
        mgr.shutdown(timeout=5)
