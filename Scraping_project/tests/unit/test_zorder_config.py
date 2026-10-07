"""#272: Z-order columns are validated against the schema, configurable, and skips are loud."""

import pytest

from src.lakehouse import lakehouse_manager as lm
from src.lakehouse.lakehouse_manager import DEFAULT_Z_ORDER_COLUMNS, LakehouseManager, _z_order_config


def _skipped(table, reason):
    if lm.DELTA_OPTIMIZE_SKIPPED is None:
        return None
    return lm.DELTA_OPTIMIZE_SKIPPED.labels(table=table, reason=reason)._value.get()


@pytest.fixture
def mgr(tmp_path):
    return LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)


def test_config_yml_supplies_the_mapping(mgr):
    assert mgr.z_order_columns["stage1_discovery"] == ["url_hash", "discovered_at"]
    assert mgr.z_order_columns["stage2_page_analysis"] == ["url_hash", "processed_at"]


def test_config_validation():
    assert _z_order_config(None) == DEFAULT_Z_ORDER_COLUMNS
    assert _z_order_config("nope") == DEFAULT_Z_ORDER_COLUMNS
    assert _z_order_config({"t": ["a", "b"], "off": [], "bad": [1]}) == {"t": ["a", "b"], "off": []}


def test_missing_columns_skip_with_warning_and_metric(mgr, caplog):
    mgr.write("stage1_discovery", [{"url": "https://uconn.edu/a", "url_hash": "1"}], async_write=False)
    before = _skipped("stage1_discovery", "zorder_missing_columns")
    with caplog.at_level("WARNING"):
        mgr._optimize_table("stage1_discovery")  # schema has no discovered_at
    assert any("Z-order skipped" in r.message and "discovered_at" in r.message for r in caplog.records)
    if before is not None:
        assert _skipped("stage1_discovery", "zorder_missing_columns") == before + 1


def test_configured_columns_are_used(mgr, monkeypatch):
    calls = []
    from deltalake import DeltaTable

    real_optimize = DeltaTable.optimize

    class Spy:
        def __init__(self, dt):
            self._real = real_optimize.__get__(dt)

        def compact(self):
            return self._real.compact()

        def z_order(self, cols):
            calls.append(list(cols))
            return self._real.z_order(cols)

    monkeypatch.setattr(DeltaTable, "optimize", property(lambda dt: Spy(dt)))
    mgr.z_order_columns["stage1_discovery"] = ["url_hash"]
    mgr.write("stage1_discovery", [{"url": "https://uconn.edu/a", "url_hash": "1"}], async_write=False)
    mgr._optimize_table("stage1_discovery")
    assert calls == [["url_hash"]]
