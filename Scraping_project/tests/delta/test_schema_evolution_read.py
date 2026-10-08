"""Delta schema evolution, read side (#301).

Rows are written with an old schema, then the table evolves (additive merge) and is read
back with the new one. The new reader must see old rows null-filled, filter on the new
columns, time-travel to the old schema, and get a clear error (not a schema dump) when it
asks for columns that don't exist at that version.
"""

import pytest

from src.lakehouse.lakehouse_manager import LakehouseManager, MissingColumnsError, _filter_columns

pytestmark = [pytest.mark.delta, pytest.mark.unit]

T = "stage2_page_analysis"
V1 = [{"url": "https://a.edu/1", "word_count": 10}, {"url": "https://b.edu/2", "word_count": 5}]
V2 = [{"url": "https://a.edu/3", "word_count": 20, "is_massive_doc": True, "lang": "en"}]


@pytest.fixture
def lake(tmp_path):
    m = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    assert m.write(T, list(V1), async_write=False)
    assert m.write(T, list(V2), async_write=False)
    yield m
    m.shutdown()


def _by_url(rows):
    return {r["url"]: r for r in rows}


def test_old_rows_are_null_filled_under_the_new_schema(lake):
    rows = _by_url(lake.read(T))
    assert set(rows) == {"https://a.edu/1", "https://b.edu/2", "https://a.edu/3"}
    assert rows["https://a.edu/1"]["is_massive_doc"] is None and rows["https://a.edu/1"]["lang"] is None
    assert rows["https://a.edu/3"]["is_massive_doc"] is True
    assert rows["https://a.edu/1"]["word_count"] == 10  # existing column keeps its type/value


def test_new_reader_can_project_and_filter_on_evolved_columns(lake):
    massive = lake.read(T, filters=[("is_massive_doc", "=", True)], columns=["url", "lang"])
    assert massive == [{"url": "https://a.edu/3", "lang": "en"}]
    # Old files have no is_massive_doc at all; they must read as null, not match.
    assert lake.read(T, filters=[("is_massive_doc", "=", False)]) == []


def test_time_travel_returns_the_old_schema(lake):
    v0 = lake.read(T, version=0)
    assert {r["url"] for r in v0} == {"https://a.edu/1", "https://b.edu/2"}
    assert all("lang" not in r and "is_massive_doc" not in r for r in v0)


@pytest.mark.parametrize(
    "kwargs,missing",
    [
        ({"columns": ["url", "nope"]}, ["nope"]),
        ({"filters": [("nope", "=", 1)]}, ["nope"]),
        ({"filters": [[("url", "=", "x")], [("ghost", ">", 1)]]}, ["ghost"]),
        ({"version": 0, "columns": ["url", "lang"]}, ["lang"]),
    ],
)
def test_unknown_columns_raise_a_clear_error(lake, kwargs, missing):
    with pytest.raises(MissingColumnsError) as exc:
        lake.read(T, **kwargs)
    err = exc.value
    assert err.missing == missing and err.table_name == T
    assert "url" in err.available
    assert str(err).startswith(T)
    if "version" in kwargs:
        assert "at version 0" in str(err)
    assert isinstance(err, ValueError)  # same family as pyarrow's ArrowInvalid, for old callers


def test_incompatible_type_is_quarantined_not_silently_coerced(lake):
    assert lake.write(T, [{"url": "https://c.edu/4", "word_count": "many"}], async_write=False)
    assert "https://c.edu/4" not in _by_url(lake.read(T))
    bad = lake.read("cast_quarantine", filters=[("source_table", "=", T)])
    assert [(r["url"], r["column"]) for r in bad] == [("https://c.edu/4", "word_count")]


def test_evolution_survives_a_reopened_manager(lake, tmp_path):
    lake.shutdown()
    fresh = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    try:
        assert fresh.write(T, [{"url": "https://d.edu/5", "word_count": 1, "lang": "de"}], async_write=False)
        rows = _by_url(fresh.read(T, columns=["url", "lang", "is_massive_doc"]))
        assert rows["https://d.edu/5"] == {"url": "https://d.edu/5", "lang": "de", "is_massive_doc": None}
        assert len(rows) == 4
    finally:
        fresh.shutdown()


@pytest.mark.parametrize(
    "filters,expected",
    [
        (None, set()),
        ([("a", "=", 1)], {"a"}),
        ([("a", "=", 1), ("b", "in", [1, 2])], {"a", "b"}),
        ([[("a", "=", 1)], [("c", "<", 3)]], {"a", "c"}),
        ("a = 1", None),
    ],
)
def test_filter_columns_parses_dnf(filters, expected):
    assert _filter_columns(filters) == expected
