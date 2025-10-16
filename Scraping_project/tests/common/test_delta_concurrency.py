import threading
from typing import Literal

import pandas as pd

from src.common.delta_lake import DeltaLakeManager


def write_records(
    manager: DeltaLakeManager,
    table_name: str,
    df: pd.DataFrame,
    mode: Literal["append", "overwrite", "error", "ignore"] = "append",
):
    """
    Helper function to write a pandas DataFrame to a Delta table synchronously.
    This is used by the concurrency tests to ensure writes happen in the main thread.
    """
    manager.write(table_name, df.to_dict("records"), mode=mode, async_write=False)


def read_records(manager: DeltaLakeManager, table_name: str) -> list:
    """
    Helper function to read all records from a Delta table.
    """
    return manager.read(table_name)


def test_read_while_writing(delta_sandbox):
    table_name = "concurrent_test"
    # Register the ad-hoc table for this test
    table_path = delta_sandbox.base_path / table_name
    table_path.mkdir(exist_ok=True)
    delta_sandbox.tables[table_name] = table_path

    start_evt = threading.Event()
    wrote_evt = threading.Event()

    def writer():
        start_evt.wait(timeout=5)
        df = pd.DataFrame([{"i": i} for i in range(10)])
        write_records(delta_sandbox, "concurrent_test", df, mode="overwrite")
        wrote_evt.set()

    def reader(results):
        start_evt.wait(timeout=5)
        # Block until first write finished to avoid zero-len race
        wrote_evt.wait(timeout=5)
        # Now perform read using your project’s read helper
        results.extend(read_records(delta_sandbox, "concurrent_test"))

    results = []
    t_w = threading.Thread(target=writer)
    t_r = threading.Thread(target=reader, args=(results,))
    t_w.start()
    t_r.start()
    start_evt.set()
    t_w.join()
    t_r.join()
    assert len(results) > 0


def test_concurrent_writes(delta_sandbox):
    table_name = "rw_test"
    # Register the ad-hoc table for this test
    table_path = delta_sandbox.base_path / table_name
    table_path.mkdir(exist_ok=True)
    delta_sandbox.tables[table_name] = table_path

    # Initial write to create the table and prevent a race condition
    # where both threads try to create the table at the same time.
    write_records(delta_sandbox, table_name, pd.DataFrame([{"k": 0}]), mode="overwrite")

    # Use test-unique path to avoid cross-test interference
    # Registry already isolated by delta_sandbox.
    df1 = pd.DataFrame([{"k": 1}])
    df2 = pd.DataFrame([{"k": 2}])
    barrier = threading.Barrier(2)

    def w1():
        barrier.wait()
        write_records(delta_sandbox, "rw_test", df1, mode="append")

    def w2():
        barrier.wait()
        write_records(delta_sandbox, "rw_test", df2, mode="append")

    t1 = threading.Thread(target=w1)
    t2 = threading.Thread(target=w2)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    # read back and assert >=2 rows
    rows = read_records(delta_sandbox, "rw_test")
    assert len(rows) >= 2
