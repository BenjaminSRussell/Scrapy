"""Supervisor behaviour of run_multiple_scouts.py (#1617).

The children are forked, so the monkeypatched target is inherited. These tests
need the ``fork`` start method (Linux default); they skip elsewhere.
"""

import multiprocessing as mp
import os
from pathlib import Path

import pytest

import run_multiple_scouts as scouts

pytestmark = pytest.mark.skipif(
    mp.get_start_method() != "fork",
    reason="supervisor tests rely on fork inheriting the patched target",
)


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    monkeypatch.setattr(scouts, "BACKOFF_BASE_SECONDS", 0.01)


def _crash_first_attempt_of_instance_zero(instance_id: int):
    marker = Path(os.environ["SCOUT_TEST_DIR"]) / f"crashed-{instance_id}"
    if instance_id == 0 and not marker.exists():
        marker.touch()
        os._exit(1)


def _always_crash(instance_id: int):
    os._exit(1)


def _finish_cleanly(instance_id: int):
    return None


def test_crashed_child_is_restarted_and_parent_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_TEST_DIR", str(tmp_path))

    exit_code = scouts.supervise(2, max_restarts=3, target=_crash_first_attempt_of_instance_zero)

    assert (tmp_path / "crashed-0").exists()  # the first attempt really crashed
    assert exit_code == 1  # a child failed, so the pod must not report success


def test_restart_budget_spent_leaves_slot_down_and_exits_nonzero():
    assert scouts.supervise(1, max_restarts=0, target=_always_crash) == 1


def test_clean_children_exit_zero():
    assert scouts.supervise(3, max_restarts=0, target=_finish_cleanly) == 0

