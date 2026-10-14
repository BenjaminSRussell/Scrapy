"""#270: Scrapy Prometheus metrics are registered with bounded label sets."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from prometheus_client import REGISTRY

from src import scrapy_prometheus as sp
from src.scrapy_prometheus import bounded_label
from src.utils.metric_labels import (
    BASE_LABELS,
    SCRAPY_FAMILIES,
    SITE_ID_FAMILIES,
    sample_labels,
    scrapy_family_errors,
)

# ``domain`` is the configured METRIC_DOMAIN token (#243), not a request host.
# ``host`` stays forbidden.
FORBIDDEN_LABELS = {"url", "uri", "path", "host", "netloc", "url_hash", "query", "item_id", "request_id"}


def _metrics_by_name():
    out = {}
    for name in dir(sp):
        obj = getattr(sp, name)
        if hasattr(obj, "_name") and hasattr(obj, "_labelnames"):
            out[obj._name] = obj
    return out


def test_expected_metrics_are_registered_with_expected_labels():
    metrics = _metrics_by_name()
    errors = scrapy_family_errors({name: tuple(metric._labelnames) for name, metric in metrics.items()})
    assert errors == [], "\n".join(errors)
    for name in SCRAPY_FAMILIES:
        assert set(BASE_LABELS) <= set(metrics[name]._labelnames)
        assert any(name in collector_name for collector_name in REGISTRY._names_to_collectors), name
    for name in SITE_ID_FAMILIES:
        assert "site_id" in metrics[name]._labelnames


def test_no_metric_uses_a_url_like_label():
    for name, metric in _metrics_by_name().items():
        bad = FORBIDDEN_LABELS.intersection(metric._labelnames)
        assert not bad, f"{name} has unbounded label(s) {bad}"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("empty_body", "empty_body"),
        ("non_html:application/vnd.ms-excel", "non_html"),
        ("error:https://uconn.edu/a?b=c", "error"),
        ("Blank Shell!", "blank_shell"),
        ("", "unknown"),
        (None, "unknown"),
        ("x" * 200, "x" * 40),
    ],
)
def test_bounded_label(raw, expected):
    assert bounded_label(raw) == expected


def test_item_scraped_skip_reason_label_is_bounded():
    ext = sp.PrometheusExtension.__new__(sp.PrometheusExtension)
    ext.runs = sp.CrawlRunState()
    spider = SimpleNamespace(name="scout_card_test")

    for i in range(50):  # 50 distinct raw reasons embedding URLs
        ext.item_scraped({"skip_reason": f"redirect_loop:https://uconn.edu/p{i}"}, spider)

    series = {
        s.labels["skip_reason"]
        for m in REGISTRY.collect()
        if m.name == "scrapy_urls_skipped"
        for s in m.samples
        if s.labels.get("spider") == "scout_card_test" and s.name.endswith("_total")
    }
    assert series == {"redirect_loop"}
    value = REGISTRY.get_sample_value(
        "scrapy_urls_skipped_total",
        sample_labels("scrapy_urls_skipped", spider="scout_card_test", skip_reason="redirect_loop"),
    )
    assert value == 50


def test_status_code_label_is_the_numeric_status_only():
    ext = sp.PrometheusExtension.__new__(sp.PrometheusExtension)
    spider = SimpleNamespace(name="status_card_test")
    for status in (200, 404, 200):
        ext.response_received(SimpleNamespace(status=status, url="https://x/"), SimpleNamespace(meta={}), spider)
    assert REGISTRY.get_sample_value(
        "scrapy_responses_total",
        sample_labels("scrapy_responses", spider="status_card_test", status_code="200"),
    ) == 2


def test_core_series_site_id_is_configured_not_a_url_or_job(monkeypatch):
    monkeypatch.delenv("TENANT_ID", raising=False)
    monkeypatch.delenv("REDIS_SITE_ID", raising=False)
    monkeypatch.setenv("SITE_ID", "tenant_a")
    monkeypatch.setenv("CRAWL_JOB_ID", "job-999")
    ext = sp.PrometheusExtension.__new__(sp.PrometheusExtension)
    spider = SimpleNamespace(name="site_label_test")
    ext.item_scraped({"url": "https://evil.example/job/123"}, spider)
    ext.spider_error(SimpleNamespace(type=RuntimeError, getErrorMessage=lambda: "x"), None, spider)
    ext.request_scheduled(SimpleNamespace(method="GET", url="https://evil.example/j"), spider)
    ext.response_received(
        SimpleNamespace(status=500, url="https://evil.example/j"),
        SimpleNamespace(meta={}),
        spider,
    )
    assert REGISTRY.get_sample_value(
        "scrapy_items_scraped_total",
        sample_labels("scrapy_items_scraped", spider="site_label_test"),
    ) == 1
    assert REGISTRY.get_sample_value(
        "scrapy_spider_errors_total",
        sample_labels("scrapy_spider_errors", spider="site_label_test", exception_type="RuntimeError"),
    ) == 1
    assert REGISTRY.get_sample_value(
        "scrapy_requests_total",
        sample_labels("scrapy_requests", spider="site_label_test", method="GET"),
    ) == 1
    series = {
        s.labels.get("site_id")
        for m in REGISTRY.collect()
        if m.name in {
            "scrapy_items_scraped",
            "scrapy_responses",
            "scrapy_requests",
            "scrapy_spider_errors",
        }
        for s in m.samples
        if s.labels.get("spider") == "site_label_test"
    }
    assert series == {"tenant_a"}
    monkeypatch.setenv("SITE_ID", "https://uconn.edu/a?job=1")
    from src.utils.redis import metric_site_id

    assert metric_site_id() == "rejected"
