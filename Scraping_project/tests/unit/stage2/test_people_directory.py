"""PersonItem schema and precision fixtures for the faculty directory mode (#642).

Positives are short structured cards. Negatives are prose, generic mailboxes,
ambiguous cards, and pages that only look like a directory.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.items import PersonItem
from src.stage2.intelligent_analyzer import IntelligentAnalyzer
from src.stage2.people_directory import PEOPLE_DIRECTORY_PARSE_ENABLED, extract_people
from src.utils import feature_flags as ff

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "people_directory"
URL = "https://uconn.edu/biology/people"

POSITIVE = {
    "schema_person.html": [
        {
            "name": "Jane Q. Doe",
            "email": "jane.doe@uconn.edu",
            "title": "Associate Professor of Biology",
            "confidence": 1.0,
        },
    ],
    "schema_heading_name.html": [
        {
            "name": "Jane Q. Doe",
            "email": "jane.doe@uconn.edu",
            "title": "Associate Professor of Biology",
            "confidence": 0.85,
        },
    ],
    "vcard.html": [
        {
            "name": "John A. Smith",
            "email": "john.smith@uconn.edu",
            "title": "Professor and Chair of History",
            "confidence": 1.0,
        },
    ],
    "faculty_card.html": [
        {
            "name": "Jane Q. Doe",
            "email": "jane.doe@uconn.edu",
            "title": "Associate Professor of Biology",
            "confidence": 0.9,
        },
    ],
    "accented_name.html": [
        {
            "name": "José A. García",
            "email": "jose.garcia@uconn.edu",
            "title": "Professor of History",
            "confidence": 0.9,
        },
    ],
    "two_people.html": [
        {
            "name": "Jane Q. Doe",
            "email": "jane.doe@uconn.edu",
            "title": "Associate Professor of Biology",
            "confidence": 0.9,
        },
        {
            "name": "John A. Smith",
            "email": "john.smith@uconn.edu",
            "title": "Professor and Chair of History",
            "confidence": 1.0,
        },
    ],
    "nested_card.html": [
        {
            "name": "Jane Q. Doe",
            "email": "jane.doe@uconn.edu",
            "title": "Associate Professor of Biology",
            "confidence": 0.9,
        },
    ],
}

NEGATIVE = [
    "news_prose.html",
    "generic_mailbox.html",
    "missing_title.html",
    "course_row.html",
    "split_fields.html",
    "department_vcard.html",
    "loose_person.html",
    "two_emails.html",
    "mismatched_email.html",
    "long_profile.html",
    "bad_title.html",
    "sentence_title.html",
    "ambiguous_names.html",
]


def _html(name: str) -> str:
    path = FIXTURES / name
    assert path.is_file(), path
    return path.read_text(encoding="utf-8")


def _records(name: str) -> list[dict]:
    return [item.to_record() for item in extract_people(_html(name), URL, enabled=True)]


def _analyzer() -> IntelligentAnalyzer:
    thresholds = SimpleNamespace(min_word_count=50, min_text_to_html_ratio=0.1, massive_doc_threshold=2000)
    return IntelligentAnalyzer(client=object(), delta=object(), thresholds=thresholds)


def test_person_item_schema_fields():
    assert set(PersonItem.fields) == {"name", "email", "title", "source_url", "confidence"}
    assert PersonItem.REQUIRED_FIELDS == ("name", "email", "title")
    assert PersonItem.MIN_CONFIDENCE == 0.85


def test_person_item_round_trip_normalizes_email():
    item = PersonItem(
        name="  Jane Q. Doe ",
        email="Jane.Doe@UConn.edu",
        title=" Professor of Biology ",
        source_url=URL,
        confidence=0.85,
    )
    item.validate()
    record = item.to_record()
    assert record == {
        "name": "Jane Q. Doe",
        "email": "jane.doe@uconn.edu",
        "title": "Professor of Biology",
        "source_url": URL,
        "confidence": 0.85,
    }


def test_unknown_person_field_is_rejected():
    with pytest.raises(KeyError):
        PersonItem(name="Jane Q. Doe", email="jane.doe@uconn.edu", title="Professor", extra="nope")


@pytest.mark.parametrize("field", PersonItem.REQUIRED_FIELDS)
def test_missing_person_field_is_named(field):
    data = {
        "name": "Jane Q. Doe",
        "email": "jane.doe@uconn.edu",
        "title": "Professor",
        "source_url": URL,
        "confidence": 1,
    }
    del data[field]
    item = PersonItem(**data)
    assert item.missing_required() == [field]
    with pytest.raises(ValueError, match=rf"missing required field\(s\): {field}$"):
        item.validate()


@pytest.mark.parametrize("email", ["not-an-email", "info@", "two@uconn.edu two@uconn.edu", ""])
def test_bad_email_fails_validation(email):
    item = PersonItem(name="Jane Q. Doe", email=email, title="Professor", source_url=URL, confidence=1)
    with pytest.raises(ValueError, match="email"):
        item.validate()


@pytest.mark.parametrize("confidence", [None, True, 0.84, 1.01, "high"])
def test_low_or_non_numeric_confidence_fails(confidence):
    item = PersonItem(
        name="Jane Q. Doe",
        email="jane.doe@uconn.edu",
        title="Professor",
        source_url=URL,
        confidence=confidence,
    )
    with pytest.raises(ValueError, match="confidence"):
        item.validate()


def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv(PEOPLE_DIRECTORY_PARSE_ENABLED, raising=False)
    assert ff.FLAGS[PEOPLE_DIRECTORY_PARSE_ENABLED][0] is False
    assert ff.get_bool(PEOPLE_DIRECTORY_PARSE_ENABLED, False, env={}, use_config=False) is False
    assert extract_people(_html("schema_person.html"), URL) == []
    assert extract_people(_html("schema_person.html"), URL, enabled=False) == []


def test_blank_html_emits_nothing():
    assert extract_people("", URL, enabled=True) == []
    assert extract_people("   ", URL, enabled=True) == []
    assert extract_people("<html></html>", URL, enabled=True) == []


@pytest.mark.parametrize("name,expected", list(POSITIVE.items()))
def test_positive_fixture_emits_only_that_person(name, expected):
    got = _records(name)
    assert len(got) == len(expected)
    for row, exp in zip(got, expected, strict=True):
        assert row["name"] == exp["name"]
        assert row["email"] == exp["email"]
        assert row["title"] == exp["title"]
        assert row["source_url"] == URL
        assert row["confidence"] == pytest.approx(exp["confidence"])
        PersonItem(**row).validate()


@pytest.mark.parametrize("name", NEGATIVE)
def test_negative_fixture_emits_nothing(name):
    assert _records(name) == []


def test_analyzer_omits_people_when_flag_is_off(monkeypatch):
    monkeypatch.delenv(PEOPLE_DIRECTORY_PARSE_ENABLED, raising=False)
    record = _analyzer()._analyze_html(URL, _html("schema_person.html"), False)
    assert "people" not in record


def test_analyzer_emits_people_when_flag_is_on(monkeypatch):
    monkeypatch.setenv(PEOPLE_DIRECTORY_PARSE_ENABLED, "on")
    record = _analyzer()._analyze_html(URL, _html("faculty_card.html"), False)
    assert len(record["people"]) == 1
    assert record["people"][0]["name"] == "Jane Q. Doe"
    assert record["people"][0]["email"] == "jane.doe@uconn.edu"
    assert record["people"][0]["confidence"] == pytest.approx(0.9)


def test_analyzer_flag_on_still_drops_prose(monkeypatch):
    monkeypatch.setenv(PEOPLE_DIRECTORY_PARSE_ENABLED, "1")
    record = _analyzer()._analyze_html(URL, _html("news_prose.html"), False)
    assert record["people"] == []
