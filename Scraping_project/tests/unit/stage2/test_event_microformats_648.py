"""schema.org Event / h-event stubs on the Stage 2 analysis JSON field (#648)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.stage2.intelligent_analyzer import IntelligentAnalyzer, events_json_field, extract_event_stubs
from src.utils import feature_flags as ff
from tests.unit.stage2.test_intelligent_analyzer import html_page, make

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "html" / "schema_org_event.html"


@pytest.fixture(autouse=True)
def no_yake(monkeypatch):
    monkeypatch.setattr(IntelligentAnalyzer, "_extract_keywords", lambda self, text, heavy: ["kw"] if text else [])


def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("STAGE2_EVENT_EXTRACT_ENABLED", raising=False)
    assert ff.FLAGS["STAGE2_EVENT_EXTRACT_ENABLED"][0] is False
    assert events_json_field("<html></html>") is None


def test_fixture_rows_land_in_analysis_events_json(monkeypatch):
    monkeypatch.setenv("STAGE2_EVENT_EXTRACT_ENABLED", "1")
    html = FIXTURE.read_text(encoding="utf-8")
    record = make()._analyze_html("https://events.example.edu/calendar", html, False)
    rows = json.loads(record["events"])
    by_name = {row["name"]: row for row in rows}
    assert by_name["Fall Open House"]["source"] == "schema.org"
    assert by_name["Fall Open House"]["start"] == "2026-10-14T15:00:00-04:00"
    assert by_name["Fall Open House"]["location"] == "Student Union"
    assert by_name["Fall Open House"]["url"] == "https://events.example.edu/open-house"
    assert by_name["Study Night"]["source"] == "h-event"
    assert by_name["Study Night"]["start"] == "2026-10-15T19:00:00-04:00"
    assert by_name["Study Night"]["location"] == "Library"
    assert by_name["Career Fair"]["source"] == "schema.org"
    assert by_name["Career Fair"]["url"] == "https://events.example.edu/career-fair"
    assert by_name["Career Fair"]["location"] == "Rome Ballroom"
    assert "Rome Ballroom" not in by_name


def test_absent_microdata_is_empty_json_and_does_not_crash(monkeypatch):
    monkeypatch.setenv("STAGE2_EVENT_EXTRACT_ENABLED", "on")
    record = make()._analyze_html("https://u.edu/plain", html_page(80), False)
    assert record["has_error"] is False
    assert json.loads(record["events"]) == []
    assert extract_event_stubs("") == []
    assert extract_event_stubs("<html><body><p>no events</p></body></html>") == []


def test_flag_off_omits_the_field(monkeypatch):
    monkeypatch.delenv("STAGE2_EVENT_EXTRACT_ENABLED", raising=False)
    record = make()._analyze_html("https://u.edu/a", FIXTURE.read_text(encoding="utf-8"), False)
    assert "events" not in record


def test_broken_jsonld_does_not_drop_a_later_event():
    html = (
        '<script type="application/ld+json">{not json</script>'
        '<script type="application/ld+json">'
        '{"@type":"EducationEvent","name":"Lab","startDate":"2026-12-01"}'
        "</script>"
    )
    rows = extract_event_stubs(html)
    assert rows == [
        {
            "source": "schema.org",
            "name": "Lab",
            "start": "2026-12-01",
            "end": "",
            "url": "",
            "location": "",
        }
    ]


def test_non_event_jsonld_is_ignored():
    html = '<script type="application/ld+json">{"@type":"Person","name":"Ada"}</script>'
    assert extract_event_stubs(html) == []


def test_stage2_worker_analysis_includes_events_json(monkeypatch):
    monkeypatch.setenv("STAGE2_EVENT_EXTRACT_ENABLED", "yes")
    from src.stage2.stage2_worker import Stage2Worker

    class _Bare:
        MIN_WORD_COUNT = 1
        MIN_TEXT_TO_HTML_RATIO = 0.0
        MASSIVE_DOC_THRESHOLD = 10**9

        async def _extract_keywords_async(self, text, is_heavy):
            return []

        async def _route_to_stage4(self, *args, **kwargs):
            raise AssertionError("fixture is not a massive doc")

        def _calculate_quality_score(self, word_count, text_ratio):
            return 0.1

    html = FIXTURE.read_text(encoding="utf-8")
    record = asyncio.run(
        Stage2Worker._analyze_html(_Bare(), "https://events.example.edu/calendar", "h", html, False)
    )
    names = {row["name"] for row in json.loads(record["events"])}
    assert names == {"Fall Open House", "Study Night", "Career Fair"}
    assert record["has_error"] is False


@pytest.mark.parametrize("raw", [None, 12, {"@type": "Event"}])
def test_odd_payloads_do_not_raise(raw):
    if raw is None:
        assert extract_event_stubs(None) == []  # type: ignore[arg-type]
        return
    assert isinstance(extract_event_stubs(str(raw)), list)
