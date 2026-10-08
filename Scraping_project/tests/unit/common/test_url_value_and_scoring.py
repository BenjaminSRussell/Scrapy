"""src/common/url_value_assessor.py + scoring_metrics.py: deterministic scores (#267)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from src.common import scoring_metrics as sm
from src.common.url_value_assessor import URLValueAssessor

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
def assessor():
    return URLValueAssessor(use_historical_data=False)


# --- scoring_metrics -------------------------------------------------------


@pytest.mark.parametrize(
    "days,k,expected",
    [(0, 0.01, 1.0), (1, 0.01, math.exp(-0.01)), (30, 0.01, math.exp(-0.3)),
     (365, 0.01, math.exp(-3.65)), (100, 0.0, 1.0), (10, 1.0, math.exp(-10))],
)
def test_decay_score_matches_formula(days, k, expected):
    pub = NOW - timedelta(days=days)
    assert sm.calculate_decay_score(pub, reference_date=NOW, decay_constant=k) == pytest.approx(expected, rel=1e-9)


def test_decay_score_accepts_iso_strings_and_naive_as_utc():
    a = sm.calculate_decay_score("2026-10-07T12:00:00Z", reference_date="2026-10-08T12:00:00+00:00")
    b = sm.calculate_decay_score(datetime(2026, 10, 7, 12), reference_date=datetime(2026, 10, 8, 12))
    assert a == pytest.approx(b) == pytest.approx(math.exp(-0.01))


def test_decay_score_huge_age_underflows_to_zero_not_negative():
    assert sm.calculate_decay_score(NOW - timedelta(days=200_000), reference_date=NOW, decay_constant=1) == 0.0


@pytest.mark.parametrize("bad", [-0.01, float("nan")])
def test_decay_score_rejects_negative_or_nan_k(bad):
    with pytest.raises(ValueError, match="decay_constant"):
        sm.calculate_decay_score(NOW - timedelta(days=1), reference_date=NOW, decay_constant=bad)


def test_decay_score_future_and_garbage_dates_raise():
    with pytest.raises(ValueError, match="future"):
        sm.calculate_decay_score(NOW + timedelta(seconds=1), reference_date=NOW)
    with pytest.raises(ValueError):
        sm.calculate_decay_score("yesterday", reference_date=NOW)


@pytest.mark.parametrize(
    "values,weights,expected",
    [([100.0, 80.0, 60.0], [0.5, 0.8, 1.0], (50 + 64 + 60) / 2.3),
     ([10.0, -10.0], [1.0, 1.0], 0.0),  # negatives are fine
     ([3.0, 9.0], [0.0, 0.0], 6.0),  # all-zero weights fall back to the plain mean
     ([5.0], [0.25], 5.0)],
)
def test_weighted_average(values, weights, expected):
    assert sm.calculate_weighted_average(values, weights) == pytest.approx(expected)


@pytest.mark.parametrize(
    "values,weights,match",
    [([], [], "empty"), ([1.0], [], "empty"), ([1.0, 2.0], [1.0], "same length"),
     ([1.0], [1.5], "range"), ([1.0], [-0.1], "range"), ([1.0], [float("nan")], "range")],
)
def test_weighted_average_rejects_bad_input(values, weights, match):
    with pytest.raises(ValueError, match=match):
        sm.calculate_weighted_average(values, weights)


def test_temporal_rank_orders_by_recency_and_requires_the_field():
    items = [{"u": "old", "publication_date": "2020-01-01T00:00:00Z"},
             {"u": "new", "publication_date": (datetime.now(UTC) - timedelta(days=1)).isoformat()}]
    ranked = sm.calculate_temporal_relevance_rank(items)
    assert [i["u"] for i in ranked] == ["new", "old"]
    assert 0.0 <= ranked[1]["recency_score"] < ranked[0]["recency_score"] <= 1.0
    with pytest.raises(ValueError, match="publication_date"):
        sm.calculate_temporal_relevance_rank([{"u": "x"}])
    assert sm.calculate_temporal_relevance_rank([]) == []


@pytest.mark.parametrize("k,half", [(0.01, 69.3147), (math.log(2), 1.0)])
def test_half_life(k, half):
    assert sm.get_decay_half_life(k) == pytest.approx(half, rel=1e-5)


@pytest.mark.parametrize("bad", [0, -1, float("nan")])
def test_half_life_rejects_non_positive(bad):
    with pytest.raises(ValueError):
        sm.get_decay_half_life(bad)


# --- URLValueAssessor -------------------------------------------------------


@pytest.mark.parametrize(
    "url,score,likelihood,spider",
    [
        ("https://www.uconn.edu/research/faculty/", 100, "high", "scout"),  # 50+30+10+10
        ("https://www.uconn.edu/login", 30, "low", "scout"),  # 50-40+10+10
        ("https://example.com/a/b/c/d/e/f", 40, "medium", "scout"),  # 50-10 deep path
        ("https://example.com/archive/2024/", 60, "medium", "depth"),
        ("https://portal.uconn.edu/app/dashboard/#/home", 85, "high", "js"),
    ],
)
def test_assess_url_scores_are_deterministic(assessor, url, score, likelihood, spider):
    a = assessor.assess_url(url)
    assert (a.value_score, a.content_likelihood, a.recommended_spider) == (score, likelihood, spider)
    assert assessor.assess_url(url) == a  # pure function of its inputs


def test_document_detection_ignores_query_and_uses_the_path(assessor):
    a = assessor.assess_url("https://example.com/docs/report.PDF?download=1")
    assert a.metadata.get("is_document") is True
    assert "document_file_pdf" in a.reasons
    b = assessor.assess_url("https://example.com/view?file=report.pdf")
    assert "is_document" not in b.metadata


@pytest.mark.parametrize("host,boost", [("www.uconn.edu", 10), ("data.gov", 10), ("www.education.com", 0),
                                        ("gov.example.org", 0), ("a.b.c.d.uconn.edu", 5)])
def test_domain_boost_uses_the_tld_not_a_substring(assessor, host, boost):
    assert assessor._assess_domain_value(host) == boost


@pytest.mark.parametrize("netloc,domain", [("news.bbc.co.uk", "bbc.co.uk"), ("www.cs.uconn.edu:8443", "uconn.edu"),
                                           ("user@sub.example.org", "example.org"), ("localhost", "localhost")])
def test_extract_domain_is_public_suffix_aware(assessor, netloc, domain):
    assert assessor._extract_domain(netloc) == domain


@pytest.mark.parametrize("depth,js", [(-5, -1.0), (0, float("nan")), ("3", "0.2"), (None, None), (50, 9.0)])
def test_boundary_inputs_never_escape_0_100(assessor, depth, js):
    a = assessor.assess_url("https://example.com/x", depth=depth, js_confidence=js)
    assert 0 <= a.value_score <= 100


def test_depth_penalty_is_linear_after_three(assessor):
    s3 = assessor.assess_url("https://example.com/a/b/c", depth=3).value_score
    s5 = assessor.assess_url("https://example.com/a/b/c", depth=5).value_score
    assert s3 - s5 == 10


@pytest.mark.parametrize(
    "conf,url,framework,spa,expected",
    [
        (0.0, "https://example.com/about", None, False, 0),
        (1.0, "https://example.com/about", None, False, 50),
        (0.5, "https://example.com/about", "React", False, 75),
        (0.5, "https://example.com/about", "unknownjs", False, 55),
        (1.0, "https://example.com/app/", "vue", True, 100),  # capped
        (-2.0, "https://example.com/about", None, False, 0),  # clamped, never negative
        (0.0, "https://example.com/apply", None, False, 0),  # "apply" is not "app"
        (0.0, "https://example.com/my-dashboard/", None, False, 10),
        (0.0, "https://portal.example.com/", None, False, 10),
    ],
)
def test_js_priority_matrix(assessor, conf, url, framework, spa, expected):
    assert assessor.calculate_js_priority(conf, url, framework, spa) == expected


def test_filter_and_batch_use_the_same_scores(assessor):
    urls = ["https://www.uconn.edu/research/", "https://www.uconn.edu/login"]
    assert assessor.filter_valuable_urls(urls, min_value_score=40) == urls[:1]
    batch = assessor.assess_batch([(u, {"depth": 0}) for u in urls])
    assert [b.value_score for b in batch] == [assessor.assess_url(u).value_score for u in urls]


def test_historical_boost_replaces_static_domain_score():
    class Hist:
        def get_domain_value_boost(self, domain):
            return -20

        def is_valuable_path(self, url, domain):
            assert domain == "uconn.edu"  # registrable domain, as stored in Delta
            return True

        def get_avg_js_confidence(self, domain):
            return 0.9

    a = URLValueAssessor(crawl_data_manager=Hist())
    r = a.assess_url("https://www.cs.uconn.edu/x")
    assert "historical_valuable_path" in r.reasons and "valuable_domain" not in r.reasons
    assert r.value_score == 50 + 35 + 10 - 20
    assert a.calculate_js_priority(0.0, "https://www.cs.uconn.edu/x") == 15
