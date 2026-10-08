"""Record factories match the Delta schemas (#275)."""
from __future__ import annotations

import pyarrow as pa
import pytest

from src.core.schemas import get_schema
from src.stage4.large_doc_processor import chunk_spans
from tests import factories


@pytest.mark.parametrize("builder,table", [
    (factories.url_record, "stage1_discovery"),
    (factories.stage2_record, "stage2_page_analysis"),
    (factories.stage3_summary, "stage3_summaries"),
    (factories.stage4_large_doc, "stage4_large_doc_summaries"),
])
def test_factory_keys_match_schema_and_convert(builder, table):
    schema = get_schema(table)
    row = builder()
    assert set(row) == set(schema.names)
    table_ = pa.Table.from_pylist([row, builder(url="https://example.com/2")], schema=schema)
    assert table_.num_rows == 2


def test_overrides_and_independence():
    a = factories.stage2_record(word_count=7, keywords=["x"])
    b = factories.stage2_record()
    assert a["word_count"] == 7 and a["keywords"] == ["x"]
    assert b["word_count"] == 500 and b["keywords"] == ["test", "sample"]
    b["keywords"].append("mutated")
    assert factories.stage2_record()["keywords"] == ["test", "sample"]  # fresh lists


def test_stage4_chunk_matches_chunk_spans():
    text = "abcdefghij" * 30
    spans = chunk_spans(text, 100, 10)
    chunks = [factories.stage4_chunk(chunk_index=i, start=s, text=text[s:e]) for i, (s, e) in enumerate(spans)]
    assert [(c["start"], c["end"]) for c in chunks] == spans
    assert all(c["text"] == text[c["start"]:c["end"]] for c in chunks)


def test_fixtures_expose_factories(make_url_record, make_stage2_record, make_stage3_summary,
                                   make_stage4_large_doc, make_stage4_chunk):
    assert make_url_record(depth=2)["depth"] == 2
    assert make_stage2_record()["title"] == "Test Page"
    assert make_stage3_summary()["summary"]
    assert make_stage4_large_doc()["is_pdf"] is True
    assert make_stage4_chunk()["chunk_index"] == 0
