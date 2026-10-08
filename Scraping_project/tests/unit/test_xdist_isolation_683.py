"""#683: filesystem fixtures stay isolated under ``pytest -n auto``.

The parametrized cases below are spread across xdist workers. Each one writes the
same file names (in tmp_path and in a Delta table in the session lake) and must read
back only its own content. Run ``python -m pytest tests/unit/test_xdist_isolation_683.py
-n 4 -o addopts=`` to exercise it with several workers. It also passes serially.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import deltalake as _DELTALAKE_AT_COLLECTION  # identity checked below
import pytest

PROJECT = Path(__file__).resolve().parents[2]
WORKER = os.environ.get("PYTEST_XDIST_WORKER", "main")
CASES = range(12)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def test_session_lake_and_dlq_are_private_temp_dirs():
    for var in ("DELTA_LAKE_PATH", "DLQ_PATH"):
        value = os.environ.get(var)
        assert value, f"{var} not isolated by tests/conftest.py"
        assert not _inside(Path(value), PROJECT), f"{var}={value} is inside the project tree"
    lake = Path(os.environ["DELTA_LAKE_PATH"])
    if lake.parent.name.startswith("scrapy-tests-"):  # set by conftest, not by the caller
        assert f"scrapy-tests-{WORKER}-" in lake.parent.name


def test_default_lakehouse_manager_uses_the_isolated_lake():
    from src.lakehouse.lakehouse_manager import LakehouseManager

    mgr = LakehouseManager(start_workers=False)
    try:
        assert mgr.base_path.resolve() == Path(os.environ["DELTA_LAKE_PATH"]).resolve()
        assert not _inside(mgr.base_path, PROJECT)
    finally:
        mgr.shutdown_event.set()


@pytest.mark.parametrize("case", CASES)
def test_identical_filenames_in_tmp_path_are_independent(tmp_path, case):
    target = tmp_path / "shared_name.json"
    payload = {"case": case, "worker": WORKER, "pid": os.getpid()}
    target.write_text(json.dumps(payload))
    time.sleep(0.01)  # widen the window in which another worker could clobber it
    assert json.loads(target.read_text()) == payload
    assert sorted(p.name for p in tmp_path.iterdir()) == ["shared_name.json"]


@pytest.mark.parametrize("case", CASES)
def test_identical_table_names_in_the_session_lake_do_not_collide(case):
    """Same table name from every case; each worker's lake is private, so no commit races."""
    from deltalake import DeltaTable

    from src.lakehouse.lakehouse_manager import LakehouseManager

    mgr = LakehouseManager(start_workers=False)
    try:
        row = {"url": f"https://www.uconn.edu/xdist/{case}", "case": case, "worker": WORKER, "pid": os.getpid()}
        assert mgr._write_sync("xdist_isolation_probe", [row], "append")
        rows = DeltaTable(str(mgr.base_path / "xdist_isolation_probe")).to_pyarrow_table().to_pylist()
        assert any(r["case"] == case for r in rows)
        # Every row in this lake came from this process: nothing shared across workers.
        assert {(r["worker"], r["pid"]) for r in rows} == {(WORKER, os.getpid())}
    finally:
        mgr.shutdown_event.set()


def test_sys_modules_mutations_are_restored_between_tests():
    """A test that drops modules from sys.modules must restore them (monkeypatch.delitem).

    Compares against the module object imported when this file was collected; a test
    that ran earlier and did a bare ``del sys.modules["deltalake"]`` makes them differ.
    """
    import sys

    assert sys.modules.get("deltalake") is _DELTALAKE_AT_COLLECTION
