"""#509: overwrite replaces rows, never silently replaces the schema."""

import threading

import pyarrow as pa
import pytest
from deltalake import DeltaTable

from src.lakehouse.lakehouse_manager import DeltaLakeManager

TABLE = "policy_table"  # unpartitioned, dynamically created


@pytest.fixture
def manager(tmp_path):
    mgr = DeltaLakeManager(base_path=str(tmp_path / "lake"), start_workers=False)
    yield mgr
    mgr.shutdown()


def _schema(mgr, table=TABLE):
    return pa.schema(DeltaTable(str(mgr.get_table_path(table))).schema().to_arrow())


def _rows(mgr, table=TABLE):
    return DeltaTable(str(mgr.get_table_path(table))).to_pyarrow_table().to_pylist()


def test_overwrite_keeps_columns_added_by_evolution(manager):
    assert manager.write(TABLE, [{"url": "a", "score": 1.5}], async_write=False)
    assert manager.write(TABLE, [{"url": "b", "score": 2.0, "lang": "en"}], async_write=False)  # evolves
    assert "lang" in _schema(manager).names

    assert manager.write(TABLE, [{"url": "c", "score": 3.0}], mode="overwrite", async_write=False)

    assert "lang" in _schema(manager).names, "overwrite clobbered an evolved column"
    rows = _rows(manager)
    assert [r["url"] for r in rows] == ["c"]  # rows replaced
    assert rows[0]["lang"] is None


def test_overwrite_keeps_existing_column_types(manager):
    manager.write(TABLE, [{"url": "a", "score": 1.5}], async_write=False)
    manager.write(TABLE, [{"url": "b", "score": 2}], mode="overwrite", async_write=False)  # int literal
    assert pa.types.is_floating(_schema(manager).field("score").type)
    assert _rows(manager)[0]["score"] == 2.0


def test_overwrite_can_still_add_columns(manager):
    manager.write(TABLE, [{"url": "a"}], async_write=False)
    manager.write(TABLE, [{"url": "b", "extra": "x"}], mode="overwrite", async_write=False)
    assert {"url", "extra"} <= set(_schema(manager).names)


def test_explicit_schema_overwrite_replaces_schema(manager, caplog):
    manager.write(TABLE, [{"url": "a", "lang": "en"}], async_write=False)
    with caplog.at_level("WARNING"):
        assert manager.write(TABLE, [{"url": "b"}], mode="overwrite", schema_overwrite=True)
    assert "lang" not in _schema(manager).names
    assert "SCHEMA OVERWRITE" in caplog.text


def test_schema_overwrite_requires_overwrite_mode(manager):
    with pytest.raises(ValueError):
        manager.write(TABLE, [{"url": "a"}], mode="append", schema_overwrite=True)


def test_schema_overwrite_never_goes_through_the_queue(manager):
    manager.write(TABLE, [{"url": "a", "lang": "en"}], async_write=False)
    manager.write(TABLE, [{"url": "b"}], mode="overwrite", async_write=True, schema_overwrite=True)
    assert manager.write_queue.qsize() == 0
    assert "lang" not in _schema(manager).names  # applied synchronously


def test_interleaved_append_and_overwrite_preserve_additive_columns(manager):
    """Appenders evolving the schema while another writer overwrites: every
    column any writer added is still present at the end."""
    manager.write(TABLE, [{"url": "seed"}], async_write=False)
    errors = []

    def appender(col):
        try:
            for i in range(3):
                manager.write(TABLE, [{"url": f"{col}{i}", col: i}], async_write=False)
        except Exception as e:  # pragma: no cover - surfaced below
            errors.append(e)

    def overwriter():
        try:
            for i in range(3):
                manager.write(TABLE, [{"url": f"ow{i}"}], mode="overwrite", async_write=False)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=appender, args=(c,)) for c in ("col_a", "col_b")]
    threads.append(threading.Thread(target=overwriter))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert {"url", "col_a", "col_b"} <= set(_schema(manager).names)
