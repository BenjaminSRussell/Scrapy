"""Stage 2 -> Stage 3 handoff contract (#283).

Stage 3 reads each ``stage2_page_analysis`` row it summarizes:
``Stage3Worker._run_traced`` filters on the quality flags and ``text_content``,
then ``_deduplicate_documents`` and ``_summarize_document`` read ``url``,
``url_hash``, ``text_content``, ``keywords`` and ``quality_score``.

The tests tie that field set to the Delta schema (``STAGE2_ANALYSIS_SCHEMA``),
the record factory and the row ``Stage2Worker._analyze_html`` really returns.
Dropping one of these fields from the producer or the schema fails a
``contract``-marked test, and CI runs that marker. Offline: no Redis, Postgres,
Kafka or network.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from typing import Any

import pytest

from src.core.schemas import get_schema
from tests.factories import stage2_record

pytestmark = pytest.mark.contract

STAGE2_TABLE = "stage2_page_analysis"

# Fields Stage 3 reads from one stage2_page_analysis row.
STAGE3_INPUT_FIELDS: tuple[str, ...] = (
    "url",
    "url_hash",
    "text_content",
    "keywords",
    "quality_score",
    "is_low_quality",
    "is_massive_doc",
    "has_error",
)

PAGE_URL = "https://www.example.edu/admissions"
PAGE_URL_HASH = "5f1c2d3e4a5b6c7d"
# About 110 words and well under the massive-doc limit: passes the default Stage 2 gates.
PAGE_HTML = (
    "<html><head><title>Admissions</title></head><body><main><p>"
    + "Students apply through the admissions office before the deadline. " * 10
    + "</p></main></body></html>"
)


def missing_fields(row: Mapping[str, Any], fields: Iterable[str]) -> list[str]:
    return [name for name in fields if name not in row]


def assert_stage3_inputs(row: Mapping[str, Any]) -> None:
    missing = missing_fields(row, STAGE3_INPUT_FIELDS)
    assert not missing, f"stage2 row lacks fields that Stage 3 reads: {missing}"


@pytest.fixture(scope="module")
def stage2_producer_row() -> dict[str, Any]:
    """The row ``Stage2Worker._analyze_html`` writes for a page Stage 3 summarizes."""
    from src.stage2.stage2_worker import Stage2Worker

    worker = Stage2Worker(max_concurrent=1)
    return asyncio.run(worker._analyze_html(PAGE_URL, PAGE_URL_HASH, PAGE_HTML, False))


def test_stage3_inputs_are_columns_of_the_stage2_table():
    columns = get_schema(STAGE2_TABLE).names
    absent = [name for name in STAGE3_INPUT_FIELDS if name not in columns]
    assert not absent, f"{STAGE2_TABLE} does not define Stage 3 input fields: {absent}"


def test_stage2_factory_row_carries_every_stage3_input():
    assert_stage3_inputs(stage2_record())


def test_stage2_producer_row_carries_every_stage3_input(stage2_producer_row):
    assert_stage3_inputs(stage2_producer_row)


def test_stage2_producer_writes_only_columns_the_table_defines(stage2_producer_row):
    extra = sorted(set(stage2_producer_row) - set(get_schema(STAGE2_TABLE).names))
    assert not extra, f"stage2 row has fields {STAGE2_TABLE} does not define: {extra}"


def test_stage2_producer_row_is_eligible_for_stage3_with_typed_fields(stage2_producer_row):
    row = stage2_producer_row
    # Stage 3 summarizes only rows that pass these flags and have text.
    assert row["is_low_quality"] is False
    assert row["is_massive_doc"] is False
    assert row["has_error"] is False
    assert isinstance(row["text_content"], str) and row["text_content"]
    assert isinstance(row["url"], str) and row["url"]
    assert isinstance(row["url_hash"], str) and row["url_hash"]
    assert isinstance(row["keywords"], list)
    assert isinstance(row["quality_score"], float)


@pytest.mark.parametrize("field", STAGE3_INPUT_FIELDS)
def test_removing_a_stage3_input_field_fails_the_contract(stage2_producer_row, field):
    broken = {key: value for key, value in stage2_producer_row.items() if key != field}
    with pytest.raises(AssertionError) as excinfo:
        assert_stage3_inputs(broken)
    assert repr(field) in str(excinfo.value)
