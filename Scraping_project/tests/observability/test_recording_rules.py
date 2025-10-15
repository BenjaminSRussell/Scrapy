# Scraping_project/tests/observability/test_recording_rules.py
import os
import unittest

import yaml


class TestRecordingRules(unittest.TestCase):
    def setUp(self):
        self.rules_path = os.path.join(
            os.path.dirname(__file__), "../../monitoring/recording_rules.yml"
        )
        with open(self.rules_path) as f:
            self.rules = yaml.safe_load(f)

    def test_kafka_lag_max_rule_exists(self):
        """
        Verify that the kafka_consumergroup_lag_max recording rule exists.
        """
        kafka_group = next(
            (g for g in self.rules["groups"] if g["name"] == "kafka_performance"), None
        )
        self.assertIsNotNone(kafka_group, "Group 'kafka_performance' not found")

        lag_max_rule = next(
            (
                r
                for r in kafka_group["rules"]
                if r["record"] == "kafka_consumergroup_lag_max"
            ),
            None,
        )
        self.assertIsNotNone(
            lag_max_rule, "Recording rule 'kafka_consumergroup_lag_max' not found"
        )

    def test_kafka_lag_max_rule_expression(self):
        """
        Verify the expression for kafka_consumergroup_lag_max is correct.
        """
        kafka_group = next(
            (g for g in self.rules["groups"] if g["name"] == "kafka_performance"), None
        )
        lag_max_rule = next(
            (
                r
                for r in kafka_group["rules"]
                if r["record"] == "kafka_consumergroup_lag_max"
            ),
            None,
        )
        self.assertIsNotNone(
            lag_max_rule,
            "Cannot test expression of missing rule 'kafka_consumergroup_lag_max'",
        )

        expr = lag_max_rule["expr"]
        self.assertIn(
            "max by (topic, group)",
            expr,
            "Rule should calculate the max lag per topic and group.",
        )
        self.assertIn(
            "label_replace",
            expr,
            "Rule must use label_replace to extract the 'group' from 'client_id'.",
        )
        self.assertIn(
            "kafka_consumer_records_lag",
            expr,
            "Rule must be based on the raw 'kafka_consumer_records_lag' metric.",
        )


if __name__ == "__main__":
    unittest.main()
