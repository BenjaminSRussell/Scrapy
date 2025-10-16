# Scraping_project/tests/observability/test_alert_intervals.py

import os
import re
import subprocess
import time
import unittest

import requests
import requests_mock
import yaml

from monitoring.metrics_exporter import test_alert_interval_path_resolution_success


def parse_duration_to_seconds(duration_str):
    """Converts a duration string like '15s', '1m' to seconds."""
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
        base_dir = os.path.dirname(__file__)
        compose_file = os.path.abspath(os.path.join(base_dir, "../..", "docker-compose.yml"))
        project_root = os.path.abspath(os.path.join(base_dir, "../.."))
        subprocess.run(
            f"sudo docker compose -f {compose_file} up -d grafana prometheus-a",
            shell=True,
            check=True,
            cwd=project_root,
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
        base_dir = os.path.dirname(__file__)
        compose_file = os.path.abspath(os.path.join(base_dir, "../..", "docker-compose.yml"))
        project_root = os.path.abspath(os.path.join(base_dir, "../.."))
        subprocess.run(
            f"sudo docker compose -f {compose_file} down",
            shell=True,
            check=True,
            cwd=project_root,
        )

    @staticmethod
    def get_min_scrape_interval():
        """
        Parses prometheus.yml to find the minimum scrape interval.
        """
        # Construct path relative to this file's location
        base_dir = os.path.dirname(__file__)
        config_path = os.path.abspath(os.path.join(base_dir, "../..", "monitoring", "prometheus.yml"))
        test_alert_interval_path_resolution_success.inc()

        with open(config_path) as f:
            config = yaml.safe_load(f)

        global_interval = parse_duration_to_seconds(config.get("global", {}).get("scrape_interval", "15s"))

        min_interval = global_interval
        for job in config.get("scrape_configs", []):
            job_interval = job.get("scrape_interval")
            if job_interval:
                min_interval = min(min_interval, parse_duration_to_seconds(job_interval))

        return min_interval

    @staticmethod
    def wait_for_grafana():
        """Waits for the Grafana API to become responsive."""
        grafana_url = "http://admin:admin@localhost:3000/api/health"
        max_attempts = 60  # Increased to 60 seconds
        for attempt in range(max_attempts):
            try:
                response = requests.get(grafana_url, timeout=5)
                if response.status_code == 200:
                    print(f"Grafana is up and running (took {attempt + 1}s).")
                    return
                else:
                    print(f"Attempt {attempt + 1}/{max_attempts}: Grafana returned status {response.status_code}")
            except requests.ConnectionError as e:
                print(f"Attempt {attempt + 1}/{max_attempts}: Grafana not yet ready ({e})")
            except requests.Timeout:
                print(f"Attempt {attempt + 1}/{max_attempts}: Grafana health check timed out")
            except Exception as e:
                print(f"Attempt {attempt + 1}/{max_attempts}: Unexpected error: {e}")
            time.sleep(1)

        # Check if Grafana container is running
        try:
            result = subprocess.run(
                "docker compose ps grafana",
                shell=True,
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )
            print(f"Grafana container status:\n{result.stdout}")

            # Check Grafana logs for errors
            logs_result = subprocess.run(
                "docker compose logs --tail=50 grafana",
                shell=True,
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )
            print(f"Recent Grafana logs:\n{logs_result.stdout}")
        except Exception as e:
            print(f"Could not get Grafana diagnostics: {e}")

        raise RuntimeError("Grafana did not become healthy in time.")

    def _validate_rules(self, rules_response):
        """
        Shared logic to validate the structure and intervals of Grafana alert rules.
        """
        self.assertGreater(len(rules_response), 0, "No Grafana alert rule groups found.")

        for group_name, rules in rules_response.items():
            self.assertIn("rules", rules)
            self.assertGreater(len(rules["rules"]), 0, f"No rules found in group '{group_name}'")
            for rule in rules["rules"]:
                # Handle different structures for online (API) vs offline (YAML)
                if "grafana_alert" in rule:  # Online mode, from API
                    evaluate_every_str = rule["grafana_alert"].get("interval", "1m")
                else:  # Offline mode, from rules.yml
                    evaluate_every_str = rule.get("every", "1m")

                evaluate_every = parse_duration_to_seconds(evaluate_every_str)
                for_duration = parse_duration_to_seconds(rule["for"])

                self.assertGreaterEqual(
                    evaluate_every,
                    self.min_scrape_interval,
                    f"Rule '{rule.get('title', rule.get('name'))}' in group '{group_name}' has evaluateEvery ({evaluate_every}s) "
                    f"< min_scrape_interval ({self.min_scrape_interval}s)",
                )

                self.assertEqual(
                    for_duration % evaluate_every,
                    0,
                    f"Rule '{rule.get('title', rule.get('name'))}' in group '{group_name}' has 'for' duration ({for_duration}s) "
                    f"that is not a multiple of evaluateEvery ({evaluate_every}s)",
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
        base_dir = os.path.dirname(__file__)
        compose_path = os.path.abspath(os.path.join(base_dir, "../..", "docker-compose.yml"))
        with open(compose_path) as f:
            compose_config = yaml.safe_load(f)

        grafana_service = compose_config["services"]["grafana"]
        volumes = grafana_service.get("volumes", [])
        expected_mount = "./monitoring/alerting:/etc/grafana/provisioning/alerting"
        self.assertIn(
            expected_mount,
            volumes,
            f"Missing expected volume mount in docker-compose.yml: {expected_mount}",
        )

        # 2. Validate and mock rules.yml
        rules_path = os.path.abspath(os.path.join(base_dir, "../..", "monitoring", "alerting", "rules.yml"))
        self.assertTrue(
            os.path.exists(rules_path),
            f"Alerting rules file not found at: {rules_path}",
        )

        with open(rules_path) as f:
            rules_content = yaml.safe_load(f)

        # Create a mock response that mimics the real API structure
        mock_api_response = {}
        for group in rules_content.get("groups", []):
            group_name = group.get("name")
            if group_name:
                mock_api_response[group_name] = {"rules": group.get("rules", [])}

        m.get(
            "http://admin:admin@localhost:3000/api/ruler/grafana/api/v1/rules",
            json=mock_api_response,
        )

        # 3. Reuse validation logic with mocked data
        grafana_rules_url = "http://admin:admin@localhost:3000/api/ruler/grafana/api/v1/rules"
        response = requests.get(grafana_rules_url)
        self.assertEqual(response.status_code, 200)

        self._validate_rules(response.json())


    def test_path_resolution_from_different_directory(self):
        """
        Ensures prometheus.yml can be found regardless of the execution directory.
        """
        original_cwd = os.getcwd()
        # Simulate running tests from a subdirectory
        os.chdir(os.path.dirname(__file__))
        try:
            # This should now pass with the new implementation
            interval = self.get_min_scrape_interval()
            self.assertIsInstance(interval, (int, float))
        finally:
            os.chdir(original_cwd)


if __name__ == "__main__":
    unittest.main()
