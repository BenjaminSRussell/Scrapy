"""#675: a missing/unparseable publication_date must not become a fabricated
recency_score of 0.5. The score stays None (freshness unknown), the outcome is
counted, and gold aggregation never turns unknown into a number."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from prometheus_client import REGISTRY
from scrapy.settings import Settings

import src.settings as project_settings
from src.pipelines import AggregationPipeline, RecencyScoringPipeline

SPIDER = SimpleNamespace(name="t")


def _metric(outcome: str) -> float:
    return REGISTRY.get_sample_value("scrapy_recency_items_total", {"outcome": outcome}) or 0.0


def _crawler(**overrides):
    return SimpleNamespace(settings=Settings({**{"RECENCY_DEFAULT_SCORE": project_settings.RECENCY_DEFAULT_SCORE}, **overrides}))


def test_project_default_is_unknown_not_half():
    assert project_settings.RECENCY_DEFAULT_SCORE is None
    pipe = RecencyScoringPipeline.from_crawler(_crawler())
    assert pipe.default_score is None


def test_missing_date_leaves_score_none_and_is_counted():
    pipe = RecencyScoringPipeline.from_crawler(_crawler())
    before = _metric("missing_date")
    item = pipe.process_item({"url": "https://x/1", "title": "no date"}, SPIDER)
    assert item["recency_score"] is None
    assert pipe.outcomes["missing_date"] == 1
    assert _metric("missing_date") == before + 1


def test_unparseable_date_leaves_score_none_and_is_counted():
    pipe = RecencyScoringPipeline.from_crawler(_crawler())
    before = _metric("unparseable_date")
    item = pipe.process_item({"url": "https://x/2", "publication_date": "not a date"}, SPIDER)
    assert item["recency_score"] is None
    assert pipe.outcomes["unparseable_date"] == 1
    assert _metric("unparseable_date") == before + 1


def test_valid_date_is_scored_in_range():
    pipe = RecencyScoringPipeline.from_crawler(_crawler())
    before = _metric("scored")
    item = pipe.process_item({"url": "https://x/3", "publication_date": "2024-01-15T00:00:00Z"}, SPIDER)
    assert isinstance(item["recency_score"], float)
    assert 0.0 <= item["recency_score"] <= 1.0
    assert _metric("scored") == before + 1


def test_missing_date_rate():
    pipe = RecencyScoringPipeline()
    for item in ({"publication_date": "2024-01-15T00:00:00Z"}, {}, {"publication_date": "garbage"}, {}):
        pipe.process_item(dict(item, url="https://x"), SPIDER)
    assert pipe.missing_date_rate() == pytest.approx(0.75)


@pytest.mark.parametrize("raw", [0.5, "0.25"])
def test_explicit_default_is_opt_in_legacy_imputation(raw):
    pipe = RecencyScoringPipeline.from_crawler(_crawler(RECENCY_DEFAULT_SCORE=raw))
    item = pipe.process_item({"url": "https://x/4"}, SPIDER)
    assert item["recency_score"] == float(raw)
    assert pipe.outcomes["missing_date"] == 1  # still counted as missing


def test_aggregation_max_recency_ignores_unknown_and_stays_none_when_all_unknown():
    agg = AggregationPipeline(enabled=True, persist=False)
    for item in (
        {"entity_id": "known", "url": "https://k/1", "recency_score": None},
        {"entity_id": "known", "url": "https://k/2", "recency_score": 0.4},
        {"entity_id": "unknown", "url": "https://u/1", "recency_score": None},
        {"entity_id": "unknown", "url": "https://u/2", "recency_score": None},
    ):
        agg.process_item(item, SPIDER)
    rows = {r["entity_id"]: r for r in agg.build_summary_rows("t")}
    assert rows["known"]["max_recency_score"] == 0.4
    assert rows["unknown"]["max_recency_score"] is None  # not a fabricated 0.0


def test_summary_context_tolerates_unknown_recency():
    agg = AggregationPipeline(enabled=True, persist=False)
    text = agg._generate_entity_summary("e", [{"title": "t", "recency_score": None, "content": None}])
    assert "e" in text
