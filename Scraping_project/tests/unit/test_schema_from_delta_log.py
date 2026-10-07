"""#226/#229: write-path schema comes from _delta_log; additive evolution; required fields enforced."""

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from src.lakehouse import lakehouse_manager as lm
from src.lakehouse.lakehouse_manager import LakehouseManager, cast_rows_to_schema


@pytest.fixture
def base(tmp_path):
    return str(tmp_path / "lake")


def _mgr(base):
    return LakehouseManager(base_path=base, start_workers=False)


def _rows(base, table):
    return sorted(DeltaTable(f"{base}/{table}").to_pyarrow_table().to_pylist(), key=lambda r: r["url"])


def test_two_writers_with_different_optional_fields_retain_both(base):
    a, b = _mgr(base), _mgr(base)  # separate processes' worth of state
    assert a._write_sync("t229", [{"url": "a1", "title": "A"}], "append")
    assert b._write_sync("t229", [{"url": "b1", "lang": "en"}], "append")
    # a's process-local view is stale; it must still keep b's column and its own.
    assert a._write_sync("t229", [{"url": "a2", "title": "A2", "lang": "fr"}], "append")
    assert b._write_sync("t229", [{"url": "b2", "lang": "de", "title": "B2"}], "append")

    rows = {r["url"]: r for r in _rows(base, "t229")}
    assert set(DeltaTable(f"{base}/t229").schema().to_arrow().names) >= {"url", "title", "lang"}
    pick = lambda r: {k: r[k] for k in ("url", "title", "lang")}  # noqa: E731
    assert pick(rows["a2"]) == {"url": "a2", "title": "A2", "lang": "fr"}
    assert pick(rows["b2"]) == {"url": "b2", "title": "B2", "lang": "de"}
    assert rows["a1"]["lang"] is None and rows["b1"]["title"] is None  # legitimately absent


def test_first_batch_all_null_column_does_not_poison_later_values(base):
    m = _mgr(base)
    # Old behaviour: first batch cached `score` as Arrow null type, so later real
    # values failed to cast and were quarantined (strict) - silent data loss.
    assert m._write_sync("t226", [{"url": "a", "score": None}], "append")
    assert m._write_sync("t226", [{"url": "b", "score": 0.75}], "append")
    rows = {r["url"]: r for r in _rows(base, "t226")}
    assert rows["b"]["score"] == 0.75
    assert lm.CAST_QUARANTINE_TABLE not in m.tables or not m.read(lm.CAST_QUARANTINE_TABLE)


def test_existing_column_types_come_from_the_table_not_the_batch(base):
    m = _mgr(base)
    write_deltalake(f"{base}/t_types", pa.table({"url": ["x"], "n": pa.array([1], pa.int32())}))
    m.tables["t_types"] = m.base_path / "t_types"
    assert m._write_sync("t_types", [{"url": "y", "n": 2}], "append")
    dt = DeltaTable(f"{base}/t_types")
    assert pa.schema(dt.schema().to_arrow()).field("n").type == pa.int32()
    assert sorted(r["n"] for r in dt.to_pyarrow_table().to_pylist()) == [1, 2]


def test_schema_evolution_is_counted(base):
    m = _mgr(base)
    m._write_sync("t_evo", [{"url": "a"}], "append")
    before = lm.DELTA_SCHEMA_EVOLUTIONS.labels(table="t_evo")._value.get() if lm.DELTA_SCHEMA_EVOLUTIONS else None
    m._write_sync("t_evo", [{"url": "b", "x": 1, "y": "z"}], "append")
    if before is not None:
        assert lm.DELTA_SCHEMA_EVOLUTIONS.labels(table="t_evo")._value.get() == before + 2


def test_required_field_is_never_null_filled():
    schema = pa.schema([pa.field("url", pa.string(), nullable=False), ("title", pa.string())])
    rows = [{"url": "ok", "title": "t"}, {"title": "no url"}, {"url": None, "title": "null url"}]
    for mode in ("strict", "coerce"):
        table, kept, failures = cast_rows_to_schema(rows, schema, mode)
        assert table.column("url").to_pylist() == ["ok"]
        assert [(f["row_index"], f["column"]) for f in failures] == [(1, "url"), (2, "url")]
        assert "required" in failures[0]["error"]


def test_required_field_missing_is_quarantined_not_written(base):
    m = _mgr(base)
    schema = pa.schema([pa.field("url", pa.string(), nullable=False), ("title", pa.string())])
    write_deltalake(f"{base}/t_req", pa.Table.from_pylist([{"url": "seed", "title": "s"}], schema=schema))
    m.tables["t_req"] = m.base_path / "t_req"
    assert m._write_sync("t_req", [{"url": "ok", "title": "a"}, {"title": "orphan"}], "append")
    assert [r["url"] for r in _rows(base, "t_req")] == ["ok", "seed"]
    q = m.read(lm.CAST_QUARANTINE_TABLE)
    assert len(q) == 1 and q[0]["column"] == "url" and q[0]["source_table"] == "t_req"
