"""#270: Scrapy Prometheus metrics are registered with bounded label sets."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from prometheus_client import REGISTRY

from src import scrapy_prometheus as sp
from src.scrapy_prometheus import bounded_label

EXPECTED = {
    # name: labels
    "scrapy_items_scraped": ("spider",),
    "scrapy_items_dropped": ("spider",),
    "scrapy_requests": ("spider", "method"),
    "scrapy_responses": ("spider", "status_code"),
    "scrapy_spider_opened": ("spider",),
    "scrapy_spider_errors": ("spider", "exception_type"),
    "scrapy_requests_dropped": ("spider", "reason"),
    "scrapy_urls_skipped": ("spider", "skip_reason"),
    "scrapy_hidden_urls_found": ("spider", "category"),
    "scrapy_hidden_urls_routed": ("spider", "route"),
    "delta_manager_shutdown": (),
}

# Label names that would carry per-URL / per-host values (unbounded cardinality).
FORBIDDEN_LABELS = {"url", "uri", "path", "host", "domain", "netloc", "url_hash", "query", "item_id", "request_id"}


def _metrics_by_name():
    out = {}
    for name in dir(sp):
        obj = getattr(sp, name)
        if hasattr(obj, "_name") and hasattr(obj, "_labelnames"):
            out[obj._name] = obj
    return out


def test_expected_metrics_are_registered_with_expected_labels():
    metrics = _metrics_by_name()
    for name, labels in EXPECTED.items():
        assert name in metrics, f"{name} not defined in scrapy_prometheus"
        assert tuple(metrics[name]._labelnames) == labels
        assert any(name in collector_name for collector_name in REGISTRY._names_to_collectors), name


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
    value = REGISTRY.get_sample_value("scrapy_urls_skipped_total", {"spider": "scout_card_test", "skip_reason": "redirect_loop"})
    assert value == 50


def test_status_code_label_is_the_numeric_status_only():
    ext = sp.PrometheusExtension.__new__(sp.PrometheusExtension)
    spider = SimpleNamespace(name="status_card_test")
    for status in (200, 404, 200):
        ext.response_received(SimpleNamespace(status=status, url="https://x/"), SimpleNamespace(meta={}), spider)
    assert REGISTRY.get_sample_value("scrapy_responses_total", {"spider": "status_card_test", "status_code": "200"}) == 2
