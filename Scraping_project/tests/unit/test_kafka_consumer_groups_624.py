"""Per-role Kafka consumer groups (#624).

No broker. Config validation rejects a shared group.id. The ingestor's
client.id shape is what makes lag alerts carry the real group label.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from src.utils.kafka_consumer_groups import (
    ROLES,
    SharedConsumerGroupError,
    client_id_for_group,
    group_for_role,
    librdkafka_config_for_role,
    require_distinct_groups,
)
from src.utils.validation import ConfigValidationError, default_config_path, validate_config_document

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
MAIN_RS = (ROOT / "kafka-delta-ingest" / "src" / "main.rs").read_text(encoding="utf-8")
SHIPPED = {
    "ingest": "scrapy-ingestor",
    "dq": "scrapy-dq",
    "replay": "scrapy-replay",
}
LEGACY = "scraping-pipeline"
LAG_ALERTS = ("KafkaHighConsumerLag", "KafkaVeryHighConsumerLag", "KafkaConsumerLagSLO")


def _shipped() -> dict:
    loaded = yaml.safe_load(default_config_path().read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_shipped_roles_are_distinct_and_not_the_retired_id():
    consumer = _shipped()["kafka"]["consumer"]
    resolved = require_distinct_groups(consumer["groups"], legacy_group_id=consumer["group_id"])
    assert resolved == SHIPPED
    assert consumer["group_id"] == LEGACY
    assert len(set(resolved.values())) == len(ROLES)
    validate_config_document(_shipped())


def test_shared_role_group_is_rejected():
    groups = dict(SHIPPED)
    groups["replay"] = groups["ingest"]
    with pytest.raises(SharedConsumerGroupError, match="shared by ingest and replay"):
        require_distinct_groups(groups, legacy_group_id=LEGACY)


def test_reusing_the_retired_shared_id_is_rejected():
    groups = dict(SHIPPED)
    groups["dq"] = LEGACY
    with pytest.raises(SharedConsumerGroupError, match="retired shared group"):
        require_distinct_groups(groups, legacy_group_id=LEGACY)
    data = copy.deepcopy(_shipped())
    data["kafka"]["consumer"]["groups"]["replay"] = LEGACY
    with pytest.raises(ConfigValidationError, match="retired shared group"):
        validate_config_document(data)


def test_config_validation_rejects_two_roles_on_one_group():
    data = copy.deepcopy(_shipped())
    data["kafka"]["consumer"]["groups"]["dq"] = data["kafka"]["consumer"]["groups"]["ingest"]
    with pytest.raises(ConfigValidationError, match="shared by ingest and dq"):
        validate_config_document(data)


def test_unknown_role_and_missing_role_are_rejected():
    with pytest.raises(SharedConsumerGroupError, match="unknown consumer role"):
        require_distinct_groups({**SHIPPED, "stage2": "scrapy-stage2"}, legacy_group_id=LEGACY)
    missing = dict(SHIPPED)
    del missing["replay"]
    with pytest.raises(SharedConsumerGroupError, match="missing replay"):
        require_distinct_groups(missing, legacy_group_id=LEGACY)
    with pytest.raises(SharedConsumerGroupError, match="unknown consumer role"):
        group_for_role("stage2", SHIPPED, legacy_group_id=LEGACY)


@pytest.mark.parametrize("role", ROLES)
def test_librdkafka_config_is_that_role_only(role):
    cfg = librdkafka_config_for_role(
        role, SHIPPED, bootstrap_servers="localhost:9092", legacy_group_id=LEGACY, instance="0"
    )
    assert cfg["group.id"] == SHIPPED[role]
    assert cfg["client.id"] == f"{SHIPPED[role]}-0"
    assert cfg["enable.auto.commit"] is False
    others = set(SHIPPED.values()) - {SHIPPED[role]}
    assert cfg["group.id"] not in others
    assert cfg["group.id"] != LEGACY


def test_client_id_suffix_must_be_one_segment():
    assert client_id_for_group("scrapy-ingestor", "4242") == "scrapy-ingestor-4242"
    with pytest.raises(SharedConsumerGroupError, match="hyphen-free"):
        client_id_for_group("scrapy-ingestor", "pod-1")


def test_ingestor_default_matches_config_and_refuses_other_roles():
    assert 'const INGEST_GROUP_ID: &str = "scrapy-ingestor"' in MAIN_RS
    assert "default_value = INGEST_GROUP_ID" in MAIN_RS
    assert '.set("client.id", &client_id)' in MAIN_RS
    assert "reject_shared_ingest_group(app_id)?" in MAIN_RS
    for reserved in ("scrapy-dq", "scrapy-replay", "scraping-pipeline"):
        assert f'"{reserved}"' in MAIN_RS
    values = yaml.safe_load(
        (ROOT / "k8s" / "helm" / "scraping-pipeline" / "values.yaml").read_text(encoding="utf-8")
    )
    assert values["kafkaDeltaIngestor"]["config"]["appId"] == SHIPPED["ingest"]


def test_lag_alerts_keep_the_group_label_and_the_slo_threshold():
    rules = yaml.safe_load((ROOT / "monitoring" / "alerting_rules.yml").read_text(encoding="utf-8"))
    helm = ROOT / "k8s" / "helm" / "scraping-pipeline" / "files" / "monitoring" / "alerting_rules.yml"
    assert helm.read_text(encoding="utf-8") == (ROOT / "monitoring" / "alerting_rules.yml").read_text(encoding="utf-8")
    found = {}
    for group in rules["groups"]:
        for rule in group.get("rules", []):
            if rule.get("alert") in LAG_ALERTS:
                found[rule["alert"]] = rule
    assert set(found) == set(LAG_ALERTS)
    for rule in found.values():
        expr = rule["expr"]
        assert "kafka_consumergroup_lag_max" in expr
        assert "without (group)" not in expr
        assert "sum(" not in expr
        assert "$labels.group" in rule["annotations"]["description"]
    assert "> 250000" in found["KafkaConsumerLagSLO"]["expr"]
    assert found["KafkaConsumerLagSLO"]["labels"]["slo"] == "true"
