# Scraping_project/tests/observability/test_alert_intervals.py

import unittest
import yaml
import requests
import os
import subprocess
import time
import re
import requests_mock

def parse_duration_to_seconds(duration_str):
    """Converts a duration string like '15s', '1m' to seconds."""
    if not isinstance(duration_str, str):
        return duration_str  # Assume it's already a number

    match = re.match(r"(\d+)([smhdw])", duration_str)
    if not match:
        raise ValueError(f"Invalid duration string: {duration_str}")

    value, unit = match.groups()
    value = int(value)

    if unit == 's':
        return value
    elif unit == 'm':
        return value * 60
    elif unit == 'h':
        return value * 3600
    # Add other units if needed
    return value


class TestAlertIntervals(unittest.TestCase):
    """
    Validates that Grafana alert evaluation intervals are aligned with
    the Prometheus scrape interval to prevent flapping alerts.
    """

    @classmethod
    def setUpClass(cls):
        """
        Starts the monitoring stack and waits for it to become healthy.
        """
        if os.environ.get("OBS_OFFLINE") == "1":
            cls.min_scrape_interval = cls.get_min_scrape_interval()
            return

        cls.min_scrape_interval = cls.get_min_scrape_interval()

        # Start docker-compose services
        print("Starting monitoring stack...")
        # Use a detached state to run in the background
        subprocess.run(
            "docker compose -f docker-compose.yml up -d grafana prometheus-a",
            shell=True, check=True, cwd="Scraping_project"
        )

        # Wait for Grafana to be healthy
        cls.wait_for_grafana()

    @classmethod
    def tearDownClass(cls):
        """
        Stops the monitoring stack.
        """
        if os.environ.get("OBS_OFFLINE") == "1":
            return

        print("Stopping monitoring stack...")
        subprocess.run(
            "docker compose -f docker-compose.yml down",
            shell=True, check=True, cwd="Scraping_project"
        )

    @staticmethod
    def get_min_scrape_interval():
        """
        Parses prometheus.yml to find the minimum scrape interval.
        """
        config_path = 'Scraping_project/monitoring/prometheus.yml'
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

        global_interval = parse_duration_to_seconds(config.get('global', {}).get('scrape_interval', '15s'))

        min_interval = global_interval
        for job in config.get('scrape_configs', []):
            job_interval = job.get('scrape_interval')
            if job_interval:
                min_interval = min(min_interval, parse_duration_to_seconds(job_interval))

        return min_interval

    @staticmethod
    def wait_for_grafana():
        """Waits for the Grafana API to become responsive."""
        grafana_url = "http://admin:admin@localhost:3000/api/health"
        for _ in range(30):  # Wait up to 30 seconds
            try:
                response = requests.get(grafana_url)
                if response.status_code == 200:
                    print("Grafana is up and running.")
                    return
            except requests.ConnectionError:
                pass
            time.sleep(1)
        raise RuntimeError("Grafana did not become healthy in time.")

    def _validate_rules(self, rules_response):
        """
        Shared logic to validate the structure and intervals of Grafana alert rules.
        """
        self.assertGreater(len(rules_response), 0, "No Grafana alert rule groups found.")

        for group_name, rules in rules_response.items():
            self.assertIn('rules', rules)
            self.assertGreater(len(rules['rules']), 0, f"No rules found in group '{group_name}'")
            for rule in rules['rules']:
                # Handle different structures for online (API) vs offline (YAML)
                if 'grafana_alert' in rule:  # Online mode, from API
                    evaluate_every_str = rule['grafana_alert'].get('interval', '1m')
                else:  # Offline mode, from rules.yml
                    evaluate_every_str = rule.get('every', '1m')

                evaluate_every = parse_duration_to_seconds(evaluate_every_str)
                for_duration = parse_duration_to_seconds(rule['for'])

                self.assertGreaterEqual(
                    evaluate_every,
                    self.min_scrape_interval,
                    f"Rule '{rule.get('title', rule.get('name'))}' in group '{group_name}' has evaluateEvery ({evaluate_every}s) "
                    f"< min_scrape_interval ({self.min_scrape_interval}s)"
                )

                self.assertEqual(
                    for_duration % evaluate_every,
                    0,
                    f"Rule '{rule.get('title', rule.get('name'))}' in group '{group_name}' has 'for' duration ({for_duration}s) "
                    f"that is not a multiple of evaluateEvery ({evaluate_every}s)"
                )

    def test_alert_intervals_are_valid_online(self):
        """
        Fetches Grafana alert rules and asserts their intervals are valid.
        """
        if os.environ.get("OBS_OFFLINE") == "1":
            self.skipTest("Skipping online test in offline mode")

        grafana_rules_url = "http://admin:admin@localhost:3000/api/ruler/grafana/api/v1/rules"

        try:
            response = requests.get(grafana_rules_url)
            response.raise_for_status()
            rules = response.json()
        except requests.exceptions.RequestException as e:
            self.fail(f"Failed to fetch Grafana rules: {e}")

        self._validate_rules(rules)

    @requests_mock.Mocker()
    def test_alert_intervals_are_valid_offline(self, m):
        """
        Statically validates alert provisioning and rule schema.
        """
        if os.environ.get("OBS_OFFLINE") != "1":
            self.skipTest("Skipping offline test in online mode")

        # 1. Validate docker-compose volume mount
        with open('Scraping_project/docker-compose.yml', 'r') as f:
            compose_config = yaml.safe_load(f)

        grafana_service = compose_config['services']['grafana']
        volumes = grafana_service.get('volumes', [])
        expected_mount = './monitoring/alerting:/etc/grafana/provisioning/alerting'
        self.assertIn(expected_mount, volumes, f"Missing expected volume mount in docker-compose.yml: {expected_mount}")

        # 2. Validate and mock rules.yml
        rules_path = 'Scraping_project/monitoring/alerting/rules.yml'
        self.assertTrue(os.path.exists(rules_path), f"Alerting rules file not found at: {rules_path}")

        with open(rules_path, 'r') as f:
            rules_content = yaml.safe_load(f)

        # Create a mock response that mimics the real API structure
        mock_api_response = {}
        for group in rules_content.get('groups', []):
            group_name = group.get('name')
            if group_name:
                mock_api_response[group_name] = {'rules': group.get('rules', [])}

        m.get("http://admin:admin@localhost:3000/api/ruler/grafana/api/v1/rules", json=mock_api_response)

        # 3. Reuse validation logic with mocked data
        grafana_rules_url = "http://admin:admin@localhost:3000/api/ruler/grafana/api/v1/rules"
        response = requests.get(grafana_rules_url)
        self.assertEqual(response.status_code, 200)

        self._validate_rules(response.json())


if __name__ == "__main__":
    unittest.main()
