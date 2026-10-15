"""Stage 2 serializer for the stage2→stage3 summary payload (#668).

Uses the fixtures in ``tests/fixtures/stage2_stage3_summary/`` via
``tests.summary_payload_fixtures``, the same loader the Stage 3 consumer tests use.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.schemas import STAGE2_ANALYSIS_SCHEMA
from src.core.summary_payload_contract import REQUIRED_SUMMARY_FIELDS, SUMMARY_FIELD_TYPES
from src.stage2.summary_payload import SummaryPayloadError, serialize_summary_payload
from tests.summary_payload_fixtures import FIXTURE_DIR, pages_contract, summary_fixture_json, summary_fixture_text

pytestmark = [pytest.mark.unit, pytest.mark.stage2, pytest.mark.contract]

_WRONG_TYPES: tuple[tuple[str, Any, str], ...] = (
    ("word_count", "420", "must be int"),
    ("word_count", True, "must be int"),
    ("word_count", -1, "must be >= 0"),
    ("content_length", 1.5, "must be int"),
    ("url", "   ", "is blank"),
    ("url_hash", "", "is blank"),
    ("text_content", 12, "must be str"),
    ("keywords", "admissions", "must be list"),
    ("keywords", ["ok", None], "is null"),
    ("keywords", ["ok", 1], "must be str"),
    ("quality_score", "0.8", "must be a number"),
    ("quality_score", True, "must be a number"),
    ("quality_score", float("nan"), "finite"),
    ("quality_score", float("inf"), "finite"),
    ("html_length", -3, "must be >= 0"),
    ("html_length", True, "must be int"),
    ("text_to_html_ratio", "0.5", "must be a number"),
    ("is_low_quality", "false", "must be bool"),
    ("has_error", 0, "must be bool"),
    ("is_massive_doc", 1, "must be bool"),
    ("title", 12, "must be str"),
    ("processed_at", 123, "must be str"),
)


def test_fixture_dir_is_the_shared_contract_dir():
    assert FIXTURE_DIR.name == "stage2_stage3_summary"
    assert (FIXTURE_DIR / "valid.json").is_file()


def test_valid_fixture_declares_required_fields_and_types():
    payload = summary_fixture_json("valid.json")
    assert tuple(payload) == REQUIRED_SUMMARY_FIELDS
    for field, types in SUMMARY_FIELD_TYPES.items():
        value = payload[field]
        if bool not in types:
            assert not isinstance(value, bool), field
        assert isinstance(value, types), field
    assert all(isinstance(item, str) for item in payload["keywords"])
    got = serialize_summary_payload(payload)
    assert got == payload
    assert got["keywords"] is not payload["keywords"]


def test_required_fields_are_stage2_analysis_columns():
    assert set(REQUIRED_SUMMARY_FIELDS) <= set(STAGE2_ANALYSIS_SCHEMA.names)


def test_required_fields_match_the_pages_contract():
    schema = pages_contract()
    assert list(REQUIRED_SUMMARY_FIELDS) == schema["required"]
    assert schema["additionalProperties"] is True
    props = schema["properties"]
    for field, types in SUMMARY_FIELD_TYPES.items():
        declared = props[field]["type"]
        if declared == "integer":
            assert types == (int,), field
        elif declared == "number":
            assert types == (int, float), field
        elif declared == "boolean":
            assert types == (bool,), field
        elif declared == "string":
            assert types == (str,), field
        elif declared == "array":
            assert types == (list,), field
            assert props[field]["items"]["type"] == "string"
        else:
            raise AssertionError(f"unexpected schema type {declared} for {field}")


def test_zero_counts_and_empty_content_are_present():
    payload = summary_fixture_json("valid.json")
    payload["word_count"] = 0
    payload["content_length"] = 0
    payload["html_length"] = 0
    payload["text_content"] = ""
    payload["title"] = ""
    payload["keywords"] = [""]
    payload["quality_score"] = 0
    assert serialize_summary_payload(payload) == payload


def test_missing_field_fixture_names_the_count():
    payload = summary_fixture_json("missing_field.json")
    assert "word_count" not in payload
    with pytest.raises(SummaryPayloadError, match="'word_count' is missing"):
        serialize_summary_payload(payload)


@pytest.mark.parametrize("field", REQUIRED_SUMMARY_FIELDS)
def test_each_missing_required_field_fails(field: str):
    payload = summary_fixture_json("valid.json")
    del payload[field]
    with pytest.raises(SummaryPayloadError, match=rf"{field!r} is missing"):
        serialize_summary_payload(payload)


def test_null_field_fixture_names_the_identifier():
    payload = summary_fixture_json("null_field.json")
    assert payload["url_hash"] is None
    with pytest.raises(SummaryPayloadError, match="'url_hash' is null"):
        serialize_summary_payload(payload)


@pytest.mark.parametrize("field", REQUIRED_SUMMARY_FIELDS)
def test_each_null_required_field_fails(field: str):
    payload = summary_fixture_json("valid.json")
    payload[field] = None
    with pytest.raises(SummaryPayloadError, match=rf"{field!r} is null"):
        serialize_summary_payload(payload)


def test_extra_fields_do_not_break_the_serializer():
    payload = summary_fixture_json("extra_field.json")
    got = serialize_summary_payload(payload)
    assert got == payload
    assert got["title"] == "Admissions"
    assert got["crawl_job_id"] == "job-668"
    assert got["is_pdf"] is False
    assert got["notes"] == {"lane": "quality"}


@pytest.mark.parametrize(("field", "value", "detail"), _WRONG_TYPES)
def test_wrong_types_fail(field: str, value: Any, detail: str):
    payload = summary_fixture_json("valid.json")
    payload[field] = value
    with pytest.raises(SummaryPayloadError, match=field) as exc:
        serialize_summary_payload(payload)
    assert detail in str(exc.value)


def test_malformed_payload_is_not_serialized():
    with pytest.raises(SummaryPayloadError, match="malformed"):
        serialize_summary_payload(summary_fixture_text("malformed.txt"))
    with pytest.raises(SummaryPayloadError, match="expected a JSON object, got list"):
        serialize_summary_payload(["not", "an", "object"])
