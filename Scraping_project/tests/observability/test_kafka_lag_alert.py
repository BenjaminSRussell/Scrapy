"""Kafka consumer-lag SLO alert (pytest; was unittest, #299)."""

from pathlib import Path

import pytest
import yaml

RULES = Path(__file__).resolve().parents[2] / "monitoring" / "alerting_rules.yml"


@pytest.fixture(scope="module")
def slo_alert():
    rules = yaml.safe_load(RULES.read_text())
    group = next((g for g in rules["groups"] if g["name"] == "kafka_infrastructure"), None)
    assert group is not None, "Group 'kafka_infrastructure' not found"
    alert = next((r for r in group["rules"] if r.get("alert") == "KafkaConsumerLagSLO"), None)
    assert alert is not None, "Alert 'KafkaConsumerLagSLO' not found"
    return alert


def test_slo_alert_uses_recording_rule(slo_alert):
    assert "kafka_consumergroup_lag_max" in slo_alert["expr"]


def test_slo_alert_has_numeric_threshold_and_hold(slo_alert):
    assert any(ch.isdigit() for ch in slo_alert["expr"])
    assert slo_alert.get("for"), "SLO alert must not fire on a single scrape"
    assert slo_alert["labels"]["severity"] == "critical"


def test_slo_alert_description_names_the_group(slo_alert):
    # The alert text relies on the recording rule producing a meaningful `group` label.
    assert "$labels.group" in slo_alert["annotations"]["description"]
