"""#535: alert on consecutive Delta write failures in kafka-delta-ingest.

This is a contract test across three files that previously disagreed. The
StatsD mapping only knew ``ingestor.*`` names, while ``main.rs`` emits
``kafka_delta_ingest.*``, so an alert on the mapped names could never fire.
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MAIN_RS = ROOT / "kafka-delta-ingest" / "src" / "main.rs"
MAPPING = ROOT / "k8s" / "helm" / "scraping-pipeline" / "files" / "monitoring" / "statsd_mapping.yml"
RULES = ROOT / "monitoring" / "alerting_rules.yml"
# Helm ships its own copy, and Helm is where statsd-exporter runs, so the
# alerts must live in both files.
HELM_RULES = ROOT / "k8s" / "helm" / "scraping-pipeline" / "files" / "monitoring" / "alerting_rules.yml"

ALERTS = ("KafkaIngestConsecutiveWriteFailures", "KafkaIngestWriteFailuresSustained")


def _alerts(path=RULES):
    rules = yaml.safe_load(path.read_text())
    return {r["alert"]: (g["name"], r) for g in rules["groups"] for r in g["rules"] if "alert" in r}


def _emitted_statsd_names():
    src = MAIN_RS.read_text()
    prefix = re.search(r'StatsdClient::from_sink\("([^"]+)"', src).group(1)
    names = set(re.findall(r'metrics\.(?:incr|count)\("([^"]+)"', src))
    return {f"{prefix}.{n}" for n in names}


def _mapping_for(statsd_name):
    """First mapping that matches, in file order (statsd_exporter semantics)."""
    for m in yaml.safe_load(MAPPING.read_text())["mappings"]:
        pat = m["match"]
        if m.get("match_type") == "regex":
            if re.fullmatch(pat, statsd_name):
                return m
        else:
            rx = "^" + re.escape(pat).replace(r"\*", "([^.]+)") + "$"
            if re.match(rx, statsd_name):
                return m
    return None


def test_alerts_exist_in_the_ingestor_group_with_sane_severity():
    alerts = _alerts()
    for name in ALERTS:
        group, rule = alerts[name]
        assert group == "kafka_pipeline"
        assert rule["labels"]["component"] == "ingestor"
        assert {"summary", "description", "action"} <= set(rule["annotations"])
    assert alerts["KafkaIngestConsecutiveWriteFailures"][1]["labels"]["severity"] == "warning"
    assert alerts["KafkaIngestWriteFailuresSustained"][1]["labels"]["severity"] == "critical"


def test_alerts_require_failures_and_no_successful_batch():
    for name in ALERTS:
        expr = _alerts()[name][1]["expr"]
        assert "kafka_delta_ingest_write_failures_total" in expr
        assert "unless" in expr and "kafka_delta_ingest_batches_written_total" in expr


def test_every_series_the_alerts_use_is_emitted_by_main_rs_and_mapped():
    emitted = _emitted_statsd_names()
    mapped = {}
    for statsd_name in emitted:
        m = _mapping_for(statsd_name)
        if m is not None:
            mapped[m["name"]] = statsd_name
    for name in ALERTS:
        expr = _alerts()[name][1]["expr"]
        for series in set(re.findall(r"kafka_delta_ingest_[a-z_]+", expr)):
            assert series in mapped, f"{series} is not produced by any emitted StatsD name via the mapping"


def test_write_failure_metric_is_emitted_on_each_failed_attempt():
    src = MAIN_RS.read_text()
    err_arm = src[src.index('error!("Failed to write batch (attempt'):]
    assert 'metrics.incr("errors.write_failed")' in err_arm[:400]


def test_specific_mappings_precede_the_catch_alls():
    mappings = yaml.safe_load(MAPPING.read_text())["mappings"]
    names = [m["match"] for m in mappings]
    # Catch-alls are the regex mappings (the ".*" fallback was removed in #178).
    first_catch_all = min((i for i, m in enumerate(mappings) if m.get("match_type") == "regex"),
                          default=len(mappings))
    assert names.index("kafka_delta_ingest.errors.write_failed") < first_catch_all
    assert names.index("kafka_delta_ingest.errors.write_failed") < names.index("kafka_delta_ingest.errors.*")


def test_helm_rules_carry_the_same_alerts():
    helm = _alerts(HELM_RULES)
    local = _alerts()
    for name in ALERTS:
        assert name in helm, f"{name} missing from the Helm-deployed rules"
        assert helm[name][1]["expr"] == local[name][1]["expr"]
        assert helm[name][1]["labels"] == local[name][1]["labels"]
