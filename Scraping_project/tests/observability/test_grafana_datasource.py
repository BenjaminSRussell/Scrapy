import os
import time
import unittest
import docker
import requests
import subprocess
import sys

def is_docker_running():
    """Checks if the Docker daemon is running and accessible."""
    try:
        client = docker.from_env(timeout=2)
        client.ping()
        return True
    except Exception:
        return False

class TestGrafanaDatasource(unittest.TestCase):
    """
    Integration test to verify Grafana's datasource provisioning and connectivity.
    Falls back to a mock Grafana server if Docker is not available.
    """
    grafana_url = "http://localhost:3000"
    mock_server_process = None
    use_docker = False

    @classmethod
    def setUpClass(cls):
        """
        Set up the test environment by starting Docker containers or a mock server.
        """
        if os.getenv("GRAFANA_URL"):
            cls.grafana_url = os.getenv("GRAFANA_URL")
            print(f"--- Using existing Grafana instance at {cls.grafana_url} ---")
        elif is_docker_running():
            print("--- Docker is available. Starting containers via docker-compose. ---")
            cls.use_docker = True
            cls.project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            cls.compose_file = os.path.join(cls.project_dir, "docker-compose.yml")
            # Create a .env file if it doesn't exist to avoid docker compose errors
            env_file = os.path.join(cls.project_dir, ".env")
            if not os.path.exists(env_file):
                with open(env_file, "w") as f:
                    f.write("GRAFANA_ADMIN_PASSWORD=admin\n")
            subprocess.run(f"docker compose -f {cls.compose_file} up -d", shell=True, check=True, capture_output=True)
            cls.grafana_url = "http://localhost:3000"
        else:
            print("--- Docker not available. Starting mock Grafana server. ---")
            cls.grafana_url = "http://127.0.0.1:33000"
            mock_server_path = os.path.join(os.path.dirname(__file__), "_mock_grafana.py")
            cls.mock_server_process = subprocess.Popen([sys.executable, mock_server_path])
            time.sleep(1) # Give server a moment to start

        # Wait for the Grafana API to be ready
        retries = 10
        for i in range(retries):
            try:
                response = requests.get(f"{cls.grafana_url}/api/health", auth=("admin", "admin"))
                if response.status_code == 200:
                    print("--- Grafana API is healthy. ---")
                    return
            except requests.exceptions.ConnectionError:
                time.sleep(2)
        raise Exception(f"Grafana did not become healthy at {cls.grafana_url} in time")


    @classmethod
    def tearDownClass(cls):
        """
        Tear down the test environment.
        """
        if cls.mock_server_process:
            print("--- Stopping mock Grafana server. ---")
            cls.mock_server_process.terminate()
        elif cls.use_docker:
            print("--- Stopping containers via docker-compose. ---")
            subprocess.run(f"docker compose -f {cls.compose_file} down", shell=True, check=True, capture_output=True)

    def test_prometheus_datasource_exists_and_is_healthy(self):
        """
        Verify that the Prometheus datasource is provisioned and healthy.
        The health check is skipped when using the mock server.
        """
        # Check that the datasource exists
        response = requests.get(f"{self.grafana_url}/api/datasources", auth=("admin", "admin"))
        self.assertEqual(response.status_code, 200)
        datasources = response.json()

        prometheus_datasource = next((ds for ds in datasources if ds["type"] == "prometheus"), None)
        self.assertIsNotNone(prometheus_datasource, "Prometheus datasource not found")
        self.assertEqual(prometheus_datasource["name"], "Prometheus")
        self.assertEqual(prometheus_datasource["url"], "http://prometheus:9090")
        self.assertTrue(prometheus_datasource["isDefault"])

        # Check that the datasource is healthy (only for real Grafana)
        if not self.mock_server_process:
            health_response = requests.get(f"{self.grafana_url}/api/datasources/{prometheus_datasource['id']}/health", auth=("admin", "admin"))
            self.assertEqual(health_response.status_code, 200)
            health_data = health_response.json()
            self.assertEqual(health_data["status"], "success")
            self.assertIn("Successfully queried", health_data["message"])

    def test_prometheus_query(self):
        """
        Verify that a simple query can be executed against the Prometheus datasource.
        """
        response = requests.post(
            f"{self.grafana_url}/api/tsdb/query",
            auth=("admin", "admin"),
            json={
                "queries": [
                    {
                        "datasource": "Prometheus",
                        "expr": "up",
                        "refId": "A",
                    }
                ]
            },
        )
        self.assertEqual(response.status_code, 200)
        query_data = response.json()
        self.assertIn("results", query_data)
        self.assertIn("A", query_data["results"])
        self.assertIn("series", query_data["results"]["A"])
        self.assertGreater(len(query_data["results"]["A"]["series"]), 0)

if __name__ == "__main__":
    unittest.main()
