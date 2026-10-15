"""#949: rough kWh from job runtime, badge, and disclaimer.

The figure is stated watts times hours. It is not a measured reading, not a
carbon inventory, and not a cloud bill. A missing runtime does not invent one.
"""

from __future__ import annotations

import math
import time as _time
from pathlib import Path
from types import SimpleNamespace

import pytest
from prometheus_client import REGISTRY

from src import scrapy_prometheus as sp
from src.utils import job_energy
from src.utils.job_energy import (
    ENERGY_DISCLAIMER,
    estimate_kwh,
    format_energy_badge,
    job_energy_scorecard,
    stated_watts,
)
from src.utils.metric_labels import job_energy_labels

ROOT = Path(__file__).resolve().parents[2]


def test_one_hour_at_the_default_draw_is_a_tenth_of_a_kwh(monkeypatch):
    monkeypatch.delenv("JOB_ENERGY_WATTS", raising=False)
    assert stated_watts("") == 100.0
    assert estimate_kwh(3600) == pytest.approx(0.1)
    assert estimate_kwh(0) == 0.0
    assert estimate_kwh(3600, watts=200) == pytest.approx(0.2)
    assert estimate_kwh(3600, watts=0) == 0.0


def test_unusable_runtime_is_not_an_invented_figure():
    for bad in (-1, True, False, "3600", None, math.nan, math.inf, object()):
        assert estimate_kwh(bad) is None
        card = job_energy_scorecard("job-a", bad)
        assert card["kwh"] is None
        assert card["runtime_seconds"] is None
        assert card["badge"] == "n/a"
        assert card["disclaimer"] == ENERGY_DISCLAIMER


def test_invalid_watts_fall_back_to_the_stated_default(monkeypatch):
    monkeypatch.delenv("JOB_ENERGY_WATTS", raising=False)
    assert estimate_kwh(3600, watts=-5) == pytest.approx(0.1)
    assert estimate_kwh(3600, watts=True) == pytest.approx(0.1)
    assert estimate_kwh(3600, watts=float("nan")) == pytest.approx(0.1)
    assert stated_watts("nope") == 100.0
    assert stated_watts("-1") == 100.0
    assert stated_watts("50") == 50.0


def test_env_override_and_badge_text(monkeypatch):
    monkeypatch.setenv("JOB_ENERGY_WATTS", "250")
    assert stated_watts() == 250.0
    assert estimate_kwh(3600) == pytest.approx(0.25)
    card = job_energy_scorecard("job-env", 3600)
    assert card["scorecard"] == "job_energy"
    assert card["crawl_job_id"] == "job-env"
    assert card["watts"] == 250.0
    assert card["badge"] == "≈ 0.250 kWh"
    assert card["disclaimer"] == ENERGY_DISCLAIMER
    assert "Not a measured" in ENERGY_DISCLAIMER
    assert "not a carbon inventory" in ENERGY_DISCLAIMER
    assert "not a cloud bill" in ENERGY_DISCLAIMER


def test_badge_rounds_without_hiding_a_zero_run():
    assert format_energy_badge(None) == "n/a"
    assert format_energy_badge(0) == "≈ 0 kWh"
    assert format_energy_badge(0.0004) == "≈ <0.001 kWh"
    assert format_energy_badge(0.1) == "≈ 0.100 kWh"
    assert format_energy_badge(1.5) == "≈ 1.50 kWh"
    assert format_energy_badge(120) == "≈ 120 kWh"
    assert job_energy_scorecard("", 0)["crawl_job_id"] == "unknown"


def test_estimator_does_not_open_a_socket_or_call_a_carbon_api():
    source = Path(job_energy.__file__).read_text(encoding="utf-8")
    for token in ("import socket", "urllib", "requests", "http.client", "carbon_hint", "urlopen"):
        assert token not in source
    shipped = (ROOT / "config.yml").read_text(encoding="utf-8")
    assert "carbon_scheduling:" in shipped


def _isolate(monkeypatch, worker: str, *, site: str, job: str | None) -> None:
    monkeypatch.setenv("WORKER_ID", worker)
    monkeypatch.setenv("METRIC_DOMAIN", "energytest")
    monkeypatch.setenv("PIPELINE_STAGE", "stage1")
    monkeypatch.setenv("SITE_ID", site)
    monkeypatch.delenv("TENANT_ID", raising=False)
    monkeypatch.delenv("REDIS_SITE_ID", raising=False)
    monkeypatch.setenv("JOB_ENERGY_WATTS", "100")
    if job is None:
        monkeypatch.delenv("CRAWL_JOB_ID", raising=False)
    else:
        monkeypatch.setenv("CRAWL_JOB_ID", job)
    from src.otel_tracing import _CRAWL_JOB_ID

    _CRAWL_JOB_ID.set(None)


def test_spider_close_records_kwh_from_runtime(monkeypatch):
    _isolate(monkeypatch, "energy-worker", site="tenant_a", job="job-energy")
    spider = SimpleNamespace(name="energy_spider", crawl_job_id="job-energy")
    ext = sp.PrometheusExtension.__new__(sp.PrometheusExtension)
    ext.runs = sp.CrawlRunState()
    ext.runs.open(spider, now=0.0)
    monkeypatch.setattr(sp, "time", SimpleNamespace(time=lambda: 3600.0, strftime=_time.strftime))
    ext.spider_closed(spider, "finished")
    labels = job_energy_labels(spider)
    assert labels["crawl_job_id"] == "job-energy"
    assert labels["site_id"] == "tenant_a"
    assert "https://" not in "".join(labels.values())
    assert REGISTRY.get_sample_value("pipeline_estimated_energy_kwh", labels) == pytest.approx(0.1)


def test_missing_or_negative_runtime_does_not_set_the_gauge(monkeypatch):
    _isolate(monkeypatch, "energy-missing", site="tenant_a", job=None)
    spider = SimpleNamespace(name="energy_missing", crawl_job_id="job-missing")
    ext = sp.PrometheusExtension.__new__(sp.PrometheusExtension)
    ext.runs = sp.CrawlRunState()
    ext.spider_closed(spider, "finished")
    labels = job_energy_labels(spider)
    assert labels["crawl_job_id"] == "job-missing"
    assert REGISTRY.get_sample_value("pipeline_estimated_energy_kwh", labels) is None

    ext.runs.open(spider, now=100.0)
    monkeypatch.setattr(sp, "time", SimpleNamespace(time=lambda: 40.0, strftime=_time.strftime))
    ext.spider_closed(spider, "finished")
    assert REGISTRY.get_sample_value("pipeline_estimated_energy_kwh", labels) is None


def test_rejected_job_id_keeps_the_energy_series(monkeypatch):
    _isolate(monkeypatch, "energy-rejected", site="https://uconn.edu/a", job=None)
    spider = SimpleNamespace(name="energy_rejected", crawl_job_id="https://jobs.example/1")
    ext = sp.PrometheusExtension.__new__(sp.PrometheusExtension)
    ext.runs = sp.CrawlRunState()
    ext.runs.open(spider, now=0.0)
    monkeypatch.setattr(sp, "time", SimpleNamespace(time=lambda: 7200.0, strftime=_time.strftime))
    ext.spider_closed(spider, "finished")
    labels = job_energy_labels(spider)
    assert labels["site_id"] == "rejected"
    assert labels["crawl_job_id"] == "rejected"
    assert REGISTRY.get_sample_value("pipeline_estimated_energy_kwh", labels) == pytest.approx(0.2)
