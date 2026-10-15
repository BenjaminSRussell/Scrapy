"""Customer-success health score per org (#966).

Activity and finished-job success rate blend into one 0–1 score. The warning
alert is not a fourth SLO.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from prometheus_client import REGISTRY

from src.utils.org_customer_health import (
    ACTIVITY_JOBS_FOR_FULL,
    ACTIVITY_PAGES_FOR_FULL,
    ACTIVITY_WEIGHT,
    SUCCESS_WEIGHT,
    WARN_BELOW,
    clear_org_health_gauges,
    publish_org_health,
    score_from_records,
    score_org,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "monitoring" / "alerting_rules.yml"
HELM_RULES = ROOT / "k8s" / "helm" / "scraping-pipeline" / "files" / "monitoring" / "alerting_rules.yml"
DASH = ROOT / "monitoring" / "dashboards" / "org_customer_health.json"
HELM_DASH = ROOT / "k8s" / "helm" / "scraping-pipeline" / "files" / "monitoring" / "dashboards" / "org_customer_health.json"


@pytest.fixture(autouse=True)
def _clear_gauges():
    clear_org_health_gauges()
    yield
    clear_org_health_gauges()


def test_weights_and_scales_are_the_documented_blend():
    assert ACTIVITY_WEIGHT + SUCCESS_WEIGHT == pytest.approx(1.0)
    assert ACTIVITY_PAGES_FOR_FULL == 100
    assert ACTIVITY_JOBS_FOR_FULL == 4
    assert WARN_BELOW == 0.5


def test_full_activity_and_success_scores_one():
    health = score_org(pages_crawled=100, succeeded_jobs=4, failed_jobs=0)
    assert health.reason == "ok"
    assert health.activity == pytest.approx(1.0)
    assert health.success_rate == pytest.approx(1.0)
    assert health.score == pytest.approx(1.0)
    assert health.below_warn is False


def test_finished_jobs_alone_are_activity_when_metering_is_empty():
    health = score_org(pages_crawled=0, succeeded_jobs=4, failed_jobs=0)
    assert health.activity == pytest.approx(1.0)
    assert health.score == pytest.approx(1.0)


def test_all_failed_jobs_stay_above_zero_and_under_the_warning():
    health = score_org(pages_crawled=0, succeeded_jobs=0, failed_jobs=4)
    assert health.activity == pytest.approx(1.0)
    assert health.success_rate == pytest.approx(0.0)
    assert health.score == pytest.approx(ACTIVITY_WEIGHT)
    assert health.below_warn is True


def test_pages_and_one_success_use_the_higher_activity_factor():
    health = score_org(pages_crawled=50, succeeded_jobs=1, failed_jobs=0)
    assert health.activity == pytest.approx(0.5)
    assert health.success_rate == pytest.approx(1.0)
    assert health.score == pytest.approx(ACTIVITY_WEIGHT * 0.5 + SUCCESS_WEIGHT)


def test_mixed_jobs_without_pages():
    health = score_org(pages_crawled=0, succeeded_jobs=1, failed_jobs=1)
    assert health.activity == pytest.approx(0.5)
    assert health.success_rate == pytest.approx(0.5)
    assert health.score == pytest.approx(0.5)
    assert health.below_warn is False


def test_no_inputs_and_pages_without_jobs_do_not_score_zero():
    idle = score_org(pages_crawled=0, succeeded_jobs=0, failed_jobs=0)
    assert idle.score is None and idle.reason == "no_activity" and idle.below_warn is False
    pages_only = score_org(pages_crawled=50, succeeded_jobs=0, failed_jobs=0)
    assert pages_only.score is None and pages_only.reason == "no_finished_jobs"
    assert pages_only.activity == pytest.approx(0.5)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pages_crawled": -1, "succeeded_jobs": 0, "failed_jobs": 0},
        {"pages_crawled": True, "succeeded_jobs": 1, "failed_jobs": 0},
        {"pages_crawled": float("nan"), "succeeded_jobs": 1, "failed_jobs": 0},
        {"pages_crawled": 0, "succeeded_jobs": -1, "failed_jobs": 0},
        {"pages_crawled": 0, "succeeded_jobs": True, "failed_jobs": 0},
    ],
)
def test_bad_numbers_are_rejected(kwargs):
    with pytest.raises(ValueError):
        score_org(**kwargs)


def test_records_sum_pages_and_ignore_open_jobs():
    scores = score_from_records(
        [
            {"org_id": "acme", "status": "succeeded", "job_id": "j1"},
            {"org_id": "acme", "status": "FAILED"},
            {"org_id": "acme", "status": "running"},
            {"org_id": "acme", "status": "cancelled"},
            {"org_id": "idleco", "status": "pending"},
            {"org_id": "beta", "status": "completed"},
        ],
        [
            {"org_id": "acme", "pages_crawled": 40, "js_renders": 9},
            {"org_id": "acme", "pages_crawled": 60},
            {"org_id": "pages-only", "pages_crawled": 10, "export_gb": 1},
        ],
    )
    assert scores["acme"].succeeded_jobs == 1
    assert scores["acme"].failed_jobs == 1
    assert scores["acme"].pages_crawled == pytest.approx(100)
    assert scores["acme"].activity == pytest.approx(1.0)
    assert scores["acme"].success_rate == pytest.approx(0.5)
    assert scores["acme"].score == pytest.approx(ACTIVITY_WEIGHT + SUCCESS_WEIGHT * 0.5)
    assert scores["beta"].score == pytest.approx(ACTIVITY_WEIGHT * 0.25 + SUCCESS_WEIGHT)
    assert scores["idleco"].reason == "no_activity"
    assert scores["pages-only"].reason == "no_finished_jobs"


def test_bad_org_unknown_status_and_meter_shape_are_rejected():
    with pytest.raises(ValueError):
        score_from_records([{"org_id": "https://evil.example/a", "status": "succeeded"}], [])
    with pytest.raises(ValueError):
        score_from_records([{"org_id": "acme", "status": "exploded"}], [])
    with pytest.raises(ValueError):
        score_from_records([], [{"org_id": "acme", "js_renders": 3}])
    with pytest.raises(ValueError):
        score_from_records([{"status": "succeeded"}], [])


def test_publish_sets_scores_and_drops_orgs_without_one():
    scores = score_from_records(
        [
            {"org_id": "acme", "status": "succeeded"},
            {"org_id": "acme", "status": "succeeded"},
            {"org_id": "acme", "status": "succeeded"},
            {"org_id": "acme", "status": "succeeded"},
            {"org_id": "quiet", "status": "running"},
        ],
        [],
    )
    publish_org_health(scores)
    assert REGISTRY.get_sample_value("org_customer_health_score", {"org_id": "acme"}) == pytest.approx(1.0)
    assert REGISTRY.get_sample_value("org_customer_health_activity", {"org_id": "acme"}) == pytest.approx(1.0)
    assert REGISTRY.get_sample_value("org_customer_health_success_ratio", {"org_id": "acme"}) == pytest.approx(1.0)
    assert REGISTRY.get_sample_value("org_customer_health_score", {"org_id": "quiet"}) is None

    publish_org_health({"beta": score_org(pages_crawled=0, succeeded_jobs=0, failed_jobs=4)})
    assert REGISTRY.get_sample_value("org_customer_health_score", {"org_id": "acme"}) is None
    assert REGISTRY.get_sample_value("org_customer_health_score", {"org_id": "beta"}) == pytest.approx(ACTIVITY_WEIGHT)


def test_warning_alert_is_not_an_slo_and_the_three_slos_stay():
    rules = yaml.safe_load(RULES.read_text())
    assert HELM_RULES.read_text() == RULES.read_text()
    groups = {g["name"]: g for g in rules["groups"]}
    warning = next(r for r in groups["org_customer_health"]["rules"] if r.get("alert") == "OrgCustomerHealthLow")
    assert warning["expr"].strip() == "org_customer_health_score < 0.5"
    assert warning["for"] == "30m"
    assert warning["labels"]["severity"] == "warning"
    assert "slo" not in warning["labels"]
    assert "OrgCustomerHealthLow" not in {r.get("alert") for r in groups["pipeline_slos"]["rules"]}
    slos = {r["alert"]: r for g in rules["groups"] for r in g["rules"] if "alert" in r}
    assert "bool" not in slos["Stage2SuccessSLO"]["expr"]
    assert "0.99" in slos["Stage2SuccessSLO"]["expr"]
    assert "> 10000" in slos["QueueBacklog"]["expr"]
    assert "> 250000" in slos["KafkaConsumerLagSLO"]["expr"]
    assert not (ROOT / "monitoring" / "alerts.yml").exists()


def test_admin_dashboard_reads_the_published_series():
    import json

    doc = json.loads(DASH.read_text())
    assert HELM_DASH.read_text() == DASH.read_text()
    assert doc["uid"] == "org-customer-health"
    exprs = [t["expr"] for p in doc["panels"] for t in p.get("targets", [])]
    blob = "\n".join(exprs)
    assert "org_customer_health_score" in blob
    assert "org_customer_health_activity" in blob
    assert "org_customer_health_success_ratio" in blob
    assert "pipeline:health:score" not in blob
    for panel in doc["panels"]:
        for target in panel.get("targets", []):
            assert target["datasource"]["type"] == "prometheus"
