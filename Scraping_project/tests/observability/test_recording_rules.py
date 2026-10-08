"""Kafka consumer-group lag recording rules (pytest; was unittest, #299).

`kafka_consumer_records_lag` (JMX) only carries `client_id`, so the rules derive `group`
with label_replace under the documented `{group}-{identifier}` convention. The old regex
`([^-]+).*` kept only the text before the *first* hyphen. Our real groups are
`scraping-pipeline` and `zsc-service`, so every consumer collapsed into "scraping" or "zsc",
and the SLO alert reported the wrong group. These tests evaluate the regex the way
Prometheus does: RE2, fully anchored, with `$1` substitution.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
RULE_FILES = [
    ROOT / "monitoring" / "recording_rules.yml",
    ROOT / "k8s" / "helm" / "scraping-pipeline" / "files" / "monitoring" / "recording_rules.yml",
]
LAG_RULES = ("kafka_consumergroup_lag_max", "kafka_consumergroup_lag_sum")
LABEL_REPLACE = re.compile(
    r'label_replace\(\s*kafka_consumer_records_lag\s*,\s*"group"\s*,\s*"\$1"\s*,\s*"client_id"\s*,\s*"([^"]+)"\s*\)'
)

# (client_id, topic, lag): the sample set the old CI job served from a mock HTTP server
# that no test ever queried, plus the project's real consumer groups.
SAMPLES = [
    ("consumer-group-a-1", "topic1", 15000),
    ("consumer-group-a-2", "topic1", 20000),
    ("consumer-group-b-1", "topic2", 5000),
    ("consumer-group-c-1", "topic3", 300000),
    ("scraping-pipeline-0", "stage2", 120),
    ("zsc-service-3", "stage2", 999),
    ("standalone", "stage3", 7),
]


def _rules(path):
    data = yaml.safe_load(path.read_text())
    group = next(g for g in data["groups"] if g["name"] == "kafka_performance")
    return {r["record"]: r["expr"] for r in group["rules"] if "record" in r}


def _group_label(regex, client_id):
    m = re.fullmatch(regex, client_id)  # Prometheus anchors label_replace regexes
    return m.expand(r"\1") if m else None


@pytest.mark.parametrize("path", RULE_FILES, ids=["monitoring", "helm"])
@pytest.mark.parametrize("record", LAG_RULES)
def test_lag_rule_shape(path, record):
    expr = _rules(path)[record]
    assert "by (topic, group)" in expr
    assert "kafka_consumer_records_lag" in expr
    assert LABEL_REPLACE.search(expr), expr


def test_helm_copy_matches_monitoring():
    assert RULE_FILES[0].read_text() == RULE_FILES[1].read_text()


@pytest.mark.parametrize("record", LAG_RULES)
@pytest.mark.parametrize(
    "client_id,group",
    [
        ("scraping-pipeline-0", "scraping-pipeline"),
        ("zsc-service-3", "zsc-service"),
        ("consumer-group-a-1", "consumer-group-a"),
        ("standalone", "standalone"),
    ],
)
def test_group_keeps_hyphenated_names(record, client_id, group):
    regex = LABEL_REPLACE.search(_rules(RULE_FILES[0])[record]).group(1)
    assert _group_label(regex, client_id) == group


def test_max_rule_and_slo_threshold_isolate_the_breaching_group():
    regex = LABEL_REPLACE.search(_rules(RULE_FILES[0])["kafka_consumergroup_lag_max"]).group(1)
    lag_max: dict[tuple[str, str], int] = {}
    for client_id, topic, lag in SAMPLES:
        key = (topic, _group_label(regex, client_id))
        lag_max[key] = max(lag_max.get(key, 0), lag)
    assert lag_max[("topic1", "consumer-group-a")] == 20000
    assert ("stage2", "scraping-pipeline") in lag_max and ("stage2", "zsc-service") in lag_max

    alert = yaml.safe_load((ROOT / "monitoring" / "alerting_rules.yml").read_text())
    expr = next(
        r["expr"] for g in alert["groups"] for r in g["rules"] if r.get("alert") == "KafkaConsumerLagSLO"
    )
    threshold = int(re.search(r">\s*(\d+)", expr).group(1))
    assert [k for k, v in lag_max.items() if v > threshold] == [("topic3", "consumer-group-c")]
