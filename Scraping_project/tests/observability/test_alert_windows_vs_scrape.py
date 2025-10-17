import os
import re
import unittest

import yaml


def parse_duration_to_seconds(duration_str):
    """Converts a duration string like '15s', '1m', '1d' to seconds."""
    if not isinstance(duration_str, str):
        return duration_str  # Assume it's already a number

    match = re.match(r"(\d+)([smhdw])", duration_str)
    if not match:
        raise ValueError(f"Invalid duration string: {duration_str}")

    value, unit = match.groups()
    value = int(value)

    if unit == "s":
        return value
    elif unit == "m":
        return value * 60
    elif unit == "h":
        return value * 3600
    elif unit == "d":
        return value * 3600 * 24
    elif unit == "w":
        return value * 3600 * 24 * 7

    return value


class TestAlertWindowsVsScrape(unittest.TestCase):
    def setUp(self):
        """
        Parses prometheus.yml to set scrape and evaluation intervals and discover rule files.
        """
        base_dir = os.path.dirname(__file__)
        self.config_path = os.path.abspath(os.path.join(base_dir, "../..", "monitoring", "prometheus.yml"))
        if not os.path.exists(self.config_path):
            self.fail(f"Prometheus config not found at: {self.config_path}")

        with open(self.config_path) as f:
            config = yaml.safe_load(f)

        global_config = config.get("global", {})
        self.scrape_interval = parse_duration_to_seconds(global_config.get("scrape_interval", "15s"))
        self.evaluation_interval = parse_duration_to_seconds(
            global_config.get("evaluation_interval", self.scrape_interval)
        )

        # Check for job-specific overrides and use the minimum scrape interval
        for job in config.get("scrape_configs", []):
            job_interval = job.get("scrape_interval")
            if job_interval:
                self.scrape_interval = min(self.scrape_interval, parse_duration_to_seconds(job_interval))

        # Discover rule files and map container paths to local paths
        self.rule_files = []
        monitoring_dir = os.path.abspath(os.path.join(base_dir, "../..", "monitoring"))
        for container_path in config.get("rule_files", []):
            # Based on docker-compose.yml, /etc/prometheus/ maps to ./monitoring/
            if container_path.startswith("/etc/prometheus/"):
                filename = os.path.basename(container_path)
                local_path = os.path.join(monitoring_dir, filename)
                self.rule_files.append(local_path)

    def test_alert_for_duration_is_valid(self):
        """
        Ensures every alert rule's 'for:' is compatible with Prometheus scrape_interval/evaluation_interval.
        """
        self.assertGreater(len(self.rule_files), 0, "No rule files were discovered from prometheus.yml")

        for rules_path in self.rule_files:
            self.assertTrue(
                os.path.exists(rules_path),
                f"Alerting rules file not found at: {rules_path}",
            )

            with open(rules_path) as f:
                rules_content = yaml.safe_load(f)

            if not rules_content or "groups" not in rules_content:
                continue  # Skip files that are not valid rule files or are empty

            for group in rules_content.get("groups", []):
                for rule in group.get("rules", []):
                    # Only check alerting rules which have a 'for' clause
                    if "alert" in rule and "for" in rule:
                        for_duration = parse_duration_to_seconds(rule["for"])
                        rule_name = rule.get("alert", "N/A")

                        # Assert: for >= 2 * scrape_interval
                        self.assertGreaterEqual(
                            for_duration,
                            2 * self.scrape_interval,
                            f"Alert rule '{rule_name}' in group '{group.get('name', 'N/A')}' in file '{rules_path}' has a 'for' duration ({for_duration}s) "
                            f"that is less than twice the scrape interval ({self.scrape_interval}s).",
                        )

                        # Assert: 'for' is a multiple of evaluation_interval
                        self.assertEqual(
                            for_duration % self.evaluation_interval,
                            0,
                            f"Alert rule '{rule_name}' in group '{group.get('name', 'N/A')}' in file '{rules_path}' has a 'for' duration ({for_duration}s) "
                            f"that is not a multiple of the evaluation interval ({self.evaluation_interval}s).",
                        )


if __name__ == "__main__":
    unittest.main()
