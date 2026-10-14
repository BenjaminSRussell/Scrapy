import json
import os
import unittest

import yaml

_BASE_DIR = os.path.dirname(__file__)
DASHBOARD_DIR = os.path.abspath(os.path.join(_BASE_DIR, "..", "..", "monitoring", "dashboards"))
RULES_DIR = os.path.abspath(os.path.join(_BASE_DIR, "..", "..", "monitoring"))

INCORRECT_LABELS = {"consumergroup", "group", "consumer_group"}
CORRECT_LABEL = "client_id"

KAFKA_CONSUMER_METRICS = [
    "kafka_consumer_records_lag",
    "kafka_consumergroup_lag",
]

class TestKafkaLabelConsistency(unittest.TestCase):
    def test_no_incorrect_labels_in_dashboards(self):
        offending_panels = []

        if not os.path.exists(DASHBOARD_DIR):
            self.skipTest(f"Dashboard directory not found: {DASHBOARD_DIR}")

        for filename in os.listdir(DASHBOARD_DIR):
            if filename.endswith(".json"):
                filepath = os.path.join(DASHBOARD_DIR, filename)
                with open(filepath) as f:
                    dashboard = json.load(f)

                for panel in dashboard.get("panels", []):
                    is_kafka_panel = False
                    if "targets" in panel:
                        for target in panel["targets"]:
                            if "expr" in target:
                                for metric in KAFKA_CONSUMER_METRICS:
                                    if metric in target["expr"]:
                                        is_kafka_panel = True
                                        break
                            if is_kafka_panel:
                                break

                    if is_kafka_panel:
                        for target in panel["targets"]:
                            for key in ["expr", "legendFormat"]:
                                if key in target:
                                    text_to_check = target[key]
                                    for label in INCORRECT_LABELS:
                                        if (
                                            f"{{{label}}}" in text_to_check
                                            or f"{{{label}=" in text_to_check
                                            or f",{label}=" in text_to_check
                                        ):
                                            offending_panels.append(
                                                f"Dashboard '{dashboard.get('title', 'N/A')}' -> Panel '{panel.get('title', 'N/A')}' -> {key}"
                                            )

        panels_message = "\n".join(offending_panels)
        self.assertEqual(
            len(offending_panels),
            0,
            f"Found incorrect labels in the following dashboard panels:\n{panels_message}",
        )

    def test_no_incorrect_labels_in_rules(self):
        offending_rules = []

        for filename in ["alerting_rules.yml", "recording_rules.yml"]:
            filepath = os.path.join(RULES_DIR, filename)
            if os.path.exists(filepath):
                with open(filepath) as f:
                    rules = yaml.safe_load(f)

                for group in rules.get("groups", []):
                    for rule in group.get("rules", []):
                        if "expr" in rule:
                            expr = rule["expr"]
                            for metric in KAFKA_CONSUMER_METRICS:
                                if metric in expr:
                                    for label in INCORRECT_LABELS:
                                        if f"{{{label}=" in expr or f",{label}=" in expr:
                                            offending_rules.append(
                                                f"Rule '{rule.get('alert', rule.get('record', 'N/A'))}' in '{filename}'"
                                            )

        rules_message = "\n".join(offending_rules)
        self.assertEqual(
            len(offending_rules),
            0,
            f"Found incorrect labels in the following rules:\n{rules_message}",
        )

    def test_scrapy_families_match_the_label_contract(self):
        """#243: crawl counters keep one stage/worker_id/domain set, and site_id."""
        try:
            from src import scrapy_prometheus as sp
            from src.utils.metric_labels import scrapy_family_errors
        except ImportError as exc:  # prometheus_client optional in some jobs
            self.skipTest(str(exc))
        metrics = {}
        for name in dir(sp):
            obj = getattr(sp, name)
            if hasattr(obj, "_name") and hasattr(obj, "_labelnames"):
                metrics[obj._name] = tuple(obj._labelnames)
        errors = scrapy_family_errors(metrics)
        self.assertEqual(errors, [], "\n".join(errors))

    def test_statsd_families_have_one_label_tuple(self):
        """A family name maps to one label set, not a choice of sets."""
        from src.utils.metric_labels import BASE_LABELS, SHARED_STATSD_FAMILIES, STATSD_FAMILIES

        for name, labels in STATSD_FAMILIES.items():
            self.assertEqual(len(labels), len(set(labels)), name)
        for name in SHARED_STATSD_FAMILIES:
            self.assertTrue(set(BASE_LABELS) <= set(STATSD_FAMILIES[name]), name)
            self.assertIn("site_id", STATSD_FAMILIES[name])


if __name__ == "__main__":
    unittest.main()
