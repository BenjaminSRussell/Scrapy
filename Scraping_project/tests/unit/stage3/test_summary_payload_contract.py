"""Stage 3 validator for the stage2→stage3 summary payload (#668).

Uses the fixtures in ``tests/fixtures/stage2_stage3_summary/`` via
``tests.summary_payload_fixtures``, the same loader the Stage 2 producer tests use.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.core.summary_payload_contract import REQUIRED_SUMMARY_FIELDS, SUMMARY_FIELD_TYPES
from src.stage2.summary_payload import SummaryPayloadError as ProducerError
from src.stage2.summary_payload import serialize_summary_payload
from src.stage3.summary_payload import SummaryPayloadError, validate_summary_payload
from tests.summary_payload_fixtures import FIXTURE_DIR, pages_contract, summary_fixture_json, summary_fixture_text

pytestmark = [pytest.mark.unit, pytest.mark.stage3, pytest.mark.contract]

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


def test_producer_and_consumer_share_one_error_type():
    assert ProducerError is SummaryPayloadError


def test_consumer_requires_the_pages_contract_fields():
    schema = pages_contract()
    assert list(REQUIRED_SUMMARY_FIELDS) == schema["required"]
    assert schema["x-delta-table"] == "stage2_page_analysis"


def test_valid_fixture_text_and_object_agree_on_types():
    text = summary_fixture_text("valid.json")
    payload = summary_fixture_json("valid.json")
    from_text = validate_summary_payload(text)
    from_object = validate_summary_payload(payload)
    assert from_text == from_object == payload
    for field, types in SUMMARY_FIELD_TYPES.items():
        value = from_text[field]
        if bool not in types:
            assert not isinstance(value, bool), field
        assert isinstance(value, types), field
    produced = serialize_summary_payload(payload)
    assert validate_summary_payload(json.dumps(produced)) == produced


def test_missing_field_fixture_names_the_count():
    text = summary_fixture_text("missing_field.json")
    payload = json.loads(text)
    assert "word_count" not in payload
    with pytest.raises(SummaryPayloadError, match="'word_count' is missing") as consumed:
        validate_summary_payload(text)
    with pytest.raises(SummaryPayloadError, match="'word_count' is missing") as produced:
        serialize_summary_payload(payload)
    assert str(consumed.value) == str(produced.value)


@pytest.mark.parametrize("field", REQUIRED_SUMMARY_FIELDS)
def test_each_missing_required_field_fails(field: str):
    payload = summary_fixture_json("valid.json")
    del payload[field]
    with pytest.raises(SummaryPayloadError, match=rf"{field!r} is missing"):
        validate_summary_payload(payload)


def test_null_field_fixture_names_the_identifier():
    text = summary_fixture_text("null_field.json")
    payload = json.loads(text)
    assert payload["url_hash"] is None
    with pytest.raises(SummaryPayloadError, match="'url_hash' is null") as consumed:
        validate_summary_payload(payload)
    with pytest.raises(SummaryPayloadError, match="'url_hash' is null") as produced:
        serialize_summary_payload(payload)
    assert str(consumed.value) == str(produced.value)


@pytest.mark.parametrize("field", REQUIRED_SUMMARY_FIELDS)
def test_each_null_required_field_fails(field: str):
    payload = summary_fixture_json("valid.json")
    payload[field] = None
    with pytest.raises(SummaryPayloadError, match=rf"{field!r} is null"):
        validate_summary_payload(payload)


def test_extra_fields_do_not_break_the_validator():
    text = summary_fixture_text("extra_field.json")
    payload = summary_fixture_json("extra_field.json")
    got = validate_summary_payload(text)
    assert got == payload == serialize_summary_payload(payload)
    assert got["title"] == "Admissions"
    assert got["crawl_job_id"] == "job-668"
    assert got["is_pdf"] is False
    assert got["notes"] == {"lane": "quality"}


def test_zero_counts_and_empty_content_are_present():
    payload = summary_fixture_json("valid.json")
    payload["word_count"] = 0
    payload["content_length"] = 0
    payload["html_length"] = 0
    payload["text_content"] = ""
    payload["title"] = ""
    payload["keywords"] = []
    payload["quality_score"] = 0
    assert validate_summary_payload(json.dumps(payload)) == payload


@pytest.mark.parametrize(("field", "value", "detail"), _WRONG_TYPES)
def test_wrong_types_fail(field: str, value: Any, detail: str):
    payload = summary_fixture_json("valid.json")
    payload[field] = value
    with pytest.raises(SummaryPayloadError, match=field) as exc:
        validate_summary_payload(payload)
    assert detail in str(exc.value)


def test_malformed_payload_errors_name_the_problem():
    with pytest.raises(SummaryPayloadError, match="malformed: invalid JSON"):
        validate_summary_payload(summary_fixture_text("malformed.txt"))
    with pytest.raises(SummaryPayloadError, match="malformed: invalid JSON"):
        validate_summary_payload(b"\xff")
    with pytest.raises(SummaryPayloadError, match="expected a JSON object, got list"):
        validate_summary_payload("[1, 2]")
    with pytest.raises(SummaryPayloadError, match="expected a JSON object, got null"):
        validate_summary_payload("null")
    with pytest.raises(SummaryPayloadError, match="expected a JSON object, got int"):
        validate_summary_payload(3)
