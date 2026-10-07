"""#818: wrong-typed values no longer poison or silently null a whole batch."""

import pyarrow as pa

from src.lakehouse import lakehouse_manager as lm
from src.lakehouse.lakehouse_manager import LakehouseManager, cast_rows_to_schema

SCHEMA = pa.schema([("url", pa.string()), ("word_count", pa.int64()), ("score", pa.float64())])


def test_clean_batch_uses_fast_path():
    rows = [{"url": "a", "word_count": 1, "score": 0.5}, {"url": "b", "word_count": 2, "score": 1.0}]
    table, kept, failures = cast_rows_to_schema(rows, SCHEMA)
    assert failures == [] and kept == rows and table.num_rows == 2


def test_strict_drops_only_the_bad_row():
    rows = [
        {"url": "good", "word_count": 10, "score": 0.1},
        {"url": "bad", "word_count": "lots", "score": 0.2},
        {"url": "good2", "word_count": 3, "score": 0.3},
    ]
    table, kept, failures = cast_rows_to_schema(rows, SCHEMA, "strict")
    assert table.column("url").to_pylist() == ["good", "good2"]
    assert table.column("word_count").to_pylist() == [10, 3]  # not nulled
    assert [(f["row_index"], f["column"]) for f in failures] == [(1, "word_count")]
    assert failures[0]["row"]["url"] == "bad"


def test_coerce_nulls_bad_value_and_keeps_row():
    rows = [{"url": "a", "word_count": 1, "score": 0.5}, {"url": "b", "word_count": "x", "score": 0.7}]
    table, kept, failures = cast_rows_to_schema(rows, SCHEMA, "coerce")
    assert table.column("url").to_pylist() == ["a", "b"]
    assert table.column("word_count").to_pylist() == [1, None]
    assert table.column("score").to_pylist() == [0.5, 0.7]
    assert len(failures) == 1


def test_coerce_still_drops_non_nullable_failures():
    schema = pa.schema([pa.field("url", pa.string(), nullable=False), ("n", pa.int64())])
    rows = [{"url": 123.5, "n": 1}, {"url": "ok", "n": 2}]
    table, kept, failures = cast_rows_to_schema(rows, schema, "coerce")
    assert table.column("url").to_pylist() == ["ok"]


def test_all_rows_bad_returns_none():
    table, kept, failures = cast_rows_to_schema([{"url": "a", "word_count": "x", "score": 0.1}], SCHEMA)
    assert table is None and kept == [] and len(failures) == 1


def test_manager_writes_good_rows_and_quarantines_bad(tmp_path):
    mgr = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    try:
        mgr._write_sync("t818", [{"url": "seed", "word_count": 1}], "append")  # caches schema
        before = (
            lm.DELTA_CAST_FAILURES.labels(table="t818", column="word_count")._value.get()
            if lm.DELTA_CAST_FAILURES is not None
            else None
        )
        mgr._write_sync(
            "t818",
            [{"url": "ok", "word_count": 2}, {"url": "https://bad.example/x", "word_count": "two"}],
            "append",
        )
        urls = sorted(r["url"] for r in mgr.read("t818"))
        assert urls == ["ok", "seed"]

        quarantine = mgr.read(lm.CAST_QUARANTINE_TABLE)
        assert len(quarantine) == 1
        q = quarantine[0]
        assert q["source_table"] == "t818" and q["column"] == "word_count"
        assert q["url"] == "https://bad.example/x" and q["error"]
        if before is not None:
            assert lm.DELTA_CAST_FAILURES.labels(table="t818", column="word_count")._value.get() == before + 1
    finally:
        mgr.shutdown()
