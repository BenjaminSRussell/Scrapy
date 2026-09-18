import os
import unittest

import yaml

class TestKafkaLagAlert(unittest.TestCase):
    def setUp(self):
        self.rules_path = os.path.join(os.path.dirname(__file__), "../../monitoring/alerting_rules.yml")
        with open(self.rules_path) as f:
            self.rules = yaml.safe_load(f)

    def test_slo_alert_exists(self):
        kafka_group = next(
            (g for g in self.rules["groups"] if g["name"] == "kafka_infrastructure"),
            None,
        )
        self.assertIsNotNone(kafka_group, "Group 'kafka_infrastructure' not found")

        slo_alert = next(
            (r for r in kafka_group["rules"] if r["alert"] == "KafkaConsumerLagSLO"),
            None,
        )
        self.assertIsNotNone(slo_alert, "Alert 'KafkaConsumerLagSLO' not found")

    def test_slo_alert_expression_uses_exporter_metric(self):
        kafka_group = next(
            (g for g in self.rules["groups"] if g["name"] == "kafka_infrastructure"),
            None,
        )
        slo_alert = next(
            (r for r in kafka_group["rules"] if r["alert"] == "KafkaConsumerLagSLO"),
            None,
        )
        self.assertIsNotNone(slo_alert, "Cannot test expression of missing alert 'KafkaConsumerLagSLO'")

        self.assertIn(
            "kafka_consumergroup_lag_max",
            slo_alert["expr"],
            "SLO alert should be based on the 'kafka_consumergroup_lag_max' recording rule.",
        )

        self.assertTrue(
            any(char.isdigit() for char in slo_alert["expr"]),
            "SLO alert expression should have a numeric threshold.",
        )

if __name__ == "__main__":
    unittest.main()
