"""Stage 3 -> Stage 4 summary handoff contract (#283).

``Stage3Worker._summarize_document`` turns one Stage 2 row into one summary row,
which Stage 3 appends to ``stage3_summaries``. Lakes written before #612 hold the
same rows under ``stage4_summaries`` (``LEGACY_TABLE_STAGE3_SUMMARIES``). Both
names share ``STAGE3_SUMMARY_SCHEMA``. docs/STAGE_MAP.md draws Stage 3's output
landing on that legacy name, which is the Stage 3 -> 4 arrow in the stage map.

Stage 4 itself reads ``stage4_large_docs`` and ``stage2_page_analysis``, not
these rows. This contract covers the summary payload the stage map places on the
Stage 3 -> 4 path.

The tests pin the summary fields, the join key and quality values it carries
over from Stage 2, and the schema registry entries for both table names.
Dropping a field from the producer, the schema or the carried-over input fails a
``contract``-marked test, and CI runs that marker. Offline: no Redis, Postgres,
Kafka or network.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.core.constants import LEGACY_TABLE_STAGE3_SUMMARIES, TABLE_STAGE3_SUMMARIES
from src.core.schemas import STAGE3_SUMMARY_SCHEMA, get_schema
from tests.factories import stage2_record, stage3_summary

pytestmark = pytest.mark.contract

# Stage 2 fields the Stage 3 summary copies unchanged: the join key and the quality values.
STAGE2_CARRIED_FIELDS: tuple[str, ...] = ("url", "url_hash", "keywords", "quality_score")

# Values that differ from the factory defaults, so a Stage 3 fallback to a default cannot pass.
STAGE2_OVERRIDES: dict[str, Any] = {
    "url_hash": "9a8b7c6d5e4f3a2b",
    "quality_score": 0.37,
    "keywords": ["admissions", "housing"],
}


@pytest.fixture(scope="module")
def stage3_worker():
    from src.stage3.stage3_worker import Stage3Worker

    return Stage3Worker(max_concurrent=1, batch_size=1)


def summarize(worker, stage2_row: dict[str, Any]) -> dict[str, Any]:
    summary = asyncio.run(worker._summarize_document(stage2_row))
    assert summary is not None, "Stage 3 dropped a document it should summarize"
    return summary


def assert_carried(summary: dict[str, Any], stage2_row: dict[str, Any]) -> None:
    for field in STAGE2_CARRIED_FIELDS:
        assert summary.get(field) == stage2_row[field], f"summary.{field} does not match the Stage 2 row"


def test_stage3_summary_factory_matches_the_summary_schema():
    assert set(stage3_summary()) == set(STAGE3_SUMMARY_SCHEMA.names)


def test_stage3_output_matches_the_summary_schema(stage3_worker):
    summary = summarize(stage3_worker, stage2_record(**STAGE2_OVERRIDES))
    assert set(summary) == set(STAGE3_SUMMARY_SCHEMA.names)


def test_summary_carries_the_stage2_join_key_and_quality_values(stage3_worker):
    stage2_row = stage2_record(**STAGE2_OVERRIDES)
    assert_carried(summarize(stage3_worker, stage2_row), stage2_row)


@pytest.mark.parametrize("field", STAGE2_CARRIED_FIELDS)
def test_dropping_a_carried_stage2_field_fails_the_contract(stage3_worker, field):
    # Stage 3 reads with .get() defaults, so a missing field does not raise. The contract must catch it.
    stage2_row = stage2_record(**STAGE2_OVERRIDES)
    broken_input = {key: value for key, value in stage2_row.items() if key != field}
    with pytest.raises(AssertionError):
        assert_carried(summarize(stage3_worker, broken_input), stage2_row)


@pytest.mark.parametrize("table", [TABLE_STAGE3_SUMMARIES, LEGACY_TABLE_STAGE3_SUMMARIES])
def test_stage3_table_names_share_the_summary_schema(table):
    assert get_schema(table).names == STAGE3_SUMMARY_SCHEMA.names
