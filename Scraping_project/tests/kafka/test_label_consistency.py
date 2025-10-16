import json
import os
import unittest

import yaml

# Directories containing dashboards and rules
DASHBOARD_DIR = "Scraping_project/monitoring/dashboards"
RULES_DIR = "Scraping_project/monitoring"

# Labels to check
INCORRECT_LABELS = {"consumergroup", "group", "consumer_group"}
CORRECT_LABEL = "client_id"

# Metrics to check for incorrect labels
KAFKA_CONSUMER_METRICS = [
    "kafka_consumer_records_lag",
    "kafka_consumergroup_lag",
]


class TestKafkaLabelConsistency(unittest.TestCase):
    def test_no_incorrect_labels_in_dashboards(self):
        """Dashboards should not use 'consumergroup' or 'group' for consumer lag metrics."""
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
        """Prometheus rules should not use 'consumergroup' or 'group' for consumer lag metrics."""
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


if __name__ == "__main__":
    unittest.main()
