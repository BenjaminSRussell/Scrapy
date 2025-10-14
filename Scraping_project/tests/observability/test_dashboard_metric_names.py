import json
import re
import unittest
from pathlib import Path
from typing import Any, Set

from ._promql_tokens import tokenize_promql

def get_canonical_metric_names() -> Set[str]:
    """
    Parses src/scrapy_prometheus.py and monitoring/metrics_exporter.py to extract all defined Prometheus metric names.
    """
    project_root = Path(__file__).resolve().parent.parent.parent
    prometheus_files = [
        project_root / "src" / "scrapy_prometheus.py",
        project_root / "monitoring" / "metrics_exporter.py"
    ]
    metric_names = set()

    pattern = re.compile(
        r"(?:Counter|Gauge|Histogram)\(\s*['\"](?P<name>[a-zA-Z_][a-zA-Z0-9_]*)['\"]"
    )

    for file_path in prometheus_files:
        if file_path.exists():
            content = file_path.read_text()
            metric_names.update(pattern.findall(content))

    return metric_names

class TestDashboardMetricNames(unittest.TestCase):
    """
    Tests that Grafana dashboards only use known Prometheus metric names.
    """

    def test_dashboard_metrics(self):
        """
        Scans all dashboards and checks their PromQL queries for unknown metrics.
        """
        project_root = Path(__file__).resolve().parent.parent.parent
        dashboards_dir = project_root / "monitoring" / "dashboards"
        dashboard_files = list(dashboards_dir.glob("*.json"))

        if not dashboard_files:
            self.skipTest(f"No dashboard files found in {dashboards_dir}")

        canonical_metrics = get_canonical_metric_names()
        builtin_metrics = {"up", "scrape_duration_seconds", "scrape_samples_scraped"}

        all_known_metrics = canonical_metrics.union(builtin_metrics)
        unknown_metrics = set()

        for dashboard_file in dashboard_files:
            with open(dashboard_file, "r") as f:
                dashboard = json.load(f)

            for panel in dashboard.get("panels", []):
                for target in panel.get("targets", []):
                    if "expr" in target:
                        expr = target["expr"]
                        tokens = tokenize_promql(expr)
                        for token in tokens:
                            if token not in all_known_metrics:
                                unknown_metrics.add(token)

        self.assertFalse(
            unknown_metrics,
            f"Found unknown metric names in dashboards: {sorted(list(unknown_metrics))}"
        )
