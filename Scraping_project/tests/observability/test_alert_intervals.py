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
    if not isinstance(duration_str, str):
        return duration_str

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
    return value

class TestAlertIntervals(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if os.environ.get("OBS_OFFLINE") == "1":
            cls.min_scrape_interval = cls.get_min_scrape_interval()
            return

        cls.min_scrape_interval = cls.get_min_scrape_interval()

        print("Starting monitoring stack...")
        base_dir = os.path.dirname(__file__)
        compose_file = os.path.abspath(os.path.join(base_dir, "../..", "docker-compose.yml"))
        project_root = os.path.abspath(os.path.join(base_dir, "../.."))
        subprocess.run(
            f"sudo docker compose -f {compose_file} up -d grafana prometheus-a",
            shell=True,
            check=True,
            cwd=project_root,
        )

        cls.wait_for_grafana()

    @classmethod
    def tearDownClass(cls):
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
        grafana_url = "http://admin:admin@localhost:3000/api/health"
        max_attempts = 60
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

        try:
            result = subprocess.run(
                "docker compose ps grafana",
                shell=True,
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )
            print(f"Grafana container status:\n{result.stdout}")

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
        self.assertGreater(len(rules_response), 0, "No Grafana alert rule groups found.")

        for group_name, rules in rules_response.items():
            self.assertIn("rules", rules)
            self.assertGreater(len(rules["rules"]), 0, f"No rules found in group '{group_name}'")
            for rule in rules["rules"]:
                if "grafana_alert" in rule:
                    evaluate_every_str = rule["grafana_alert"].get("interval", "1m")
                else:
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
        if os.environ.get("OBS_OFFLINE") != "1":
            self.skipTest("Skipping offline test in online mode")

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

        rules_path = os.path.abspath(os.path.join(base_dir, "../..", "monitoring", "alerting", "rules.yml"))
        self.assertTrue(
            os.path.exists(rules_path),
            f"Alerting rules file not found at: {rules_path}",
        )

        with open(rules_path) as f:
            rules_content = yaml.safe_load(f)

        mock_api_response = {}
        for group in rules_content.get("groups", []):
            group_name = group.get("name")
            if group_name:
                mock_api_response[group_name] = {"rules": group.get("rules", [])}

        m.get(
            "http://admin:admin@localhost:3000/api/ruler/grafana/api/v1/rules",
            json=mock_api_response,
        )

        grafana_rules_url = "http://admin:admin@localhost:3000/api/ruler/grafana/api/v1/rules"
        response = requests.get(grafana_rules_url)
        self.assertEqual(response.status_code, 200)

        self._validate_rules(response.json())

    def test_path_resolution_from_different_directory(self):
        original_cwd = os.getcwd()
        os.chdir(os.path.dirname(__file__))
        try:
            interval = self.get_min_scrape_interval()
            self.assertIsInstance(interval, (int, float))
        finally:
            os.chdir(original_cwd)

if __name__ == "__main__":
    unittest.main()
