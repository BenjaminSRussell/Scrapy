import json
import os
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

# Configuration: Paths and Metric Name Rules
DASHBOARD_PATHS = [
    "Scraping_project/monitoring/dashboards/",
    "Scraping_project/ops/grafana/dashboards/",
]
METRIC_PREFIX_ALLOWLIST = {"scrapy_", "app_", "kafka_"}
METRIC_NAME_DENYLIST = {"scrapy_pages_scraped_total", "old_request_errors_total"}
# Add prefixes for common exporters to avoid false positives on valid dashboards
KNOWN_THIRD_PARTY_PREFIXES = {"node_", "container_", "go_", "prometheus_"}


def find_dashboard_files() -> List[Path]:
    """Finds all Grafana dashboard JSON files in the configured paths."""
    found_files = []
    for path_str in DASHBOARD_PATHS:
        path = Path(path_str)
        if path.is_dir():
            found_files.extend(path.rglob("*.json"))
    return found_files


def extract_prometheus_expressions(panel: Dict) -> List[str]:
    """Recursively extracts Prometheus 'expr' values from a panel's targets."""
    expressions = []
    if "targets" in panel:
        for target in panel.get("targets", []):
            if "expr" in target and target.get("datasource", {}).get("type") == "prometheus":
                expressions.append(target["expr"])

    if "panels" in panel:
        for sub_panel in panel.get("panels", []):
            expressions.extend(extract_prometheus_expressions(sub_panel))

    return expressions


PROMQL_KEYWORDS = {
    # Functions
    "abs", "absent", "avg_over_time", "ceil", "changes", "clamp_max", "clamp_min",
    "count_over_time", "days_in_month", "day_of_month", "day_of_week", "delta",
    "deriv", "exp", "floor", "histogram_quantile", "holt_winters", "hour",
    "idelta", "increase", "irate", "label_join", "label_replace", "ln", "log10",
    "log2", "max_over_time", "min_over_time", "minute", "month", "predict_linear",
    "quantile_over_time", "rate", "resets", "round", "scalar", "sort", "sort_desc",
    "sqrt", "stddev_over_time", "stdvar_over_time", "sum_over_time", "time",
    "timestamp", "vector", "year",
    # Aggregation operators
    "sum", "min", "max", "avg", "group", "stddev", "stdvar", "count",
    "count_values", "bottomk", "topk", "quantile",
    # Keywords
    "by", "without", "on", "ignoring", "group_left", "group_right",
}


def get_all_metric_names_from_expr(expr: str) -> Set[str]:
    """
    Extracts potential metric names from a PromQL expression, filtering out keywords.
    """
    # This regex finds things that look like metric names or functions.
    potential_names = set(re.findall(r"\b([a-zA-Z_:][a-zA-Z0-9_:]+)\b", expr))

    # Filter out known PromQL keywords/functions and pure numbers.
    metric_names = {
        name for name in potential_names
        if name not in PROMQL_KEYWORDS and not name.isdigit()
    }
    return metric_names


# Discover dashboards at the module level to allow for skipping if none are found.
dashboard_files = find_dashboard_files()
if not dashboard_files:
    pytest.skip("No Grafana dashboards found to test.", allow_module_level=True)


@pytest.mark.parametrize("dashboard_path", dashboard_files)
def test_grafana_dashboard_validity(dashboard_path: Path):
    """
    Tests a single Grafana dashboard for JSON validity and panel correctness.
    """
    try:
        with open(dashboard_path, "r") as f:
            dashboard_json = json.load(f)
    except json.JSONDecodeError:
        pytest.fail(f"Invalid JSON in dashboard: {dashboard_path}")

    assert "panels" in dashboard_json or "rows" in dashboard_json, "Dashboard must have panels or rows"

    all_panels = dashboard_json.get("panels", [])
    for row in dashboard_json.get("rows", []):
        all_panels.extend(row.get("panels", []))

    invalid_panels: List[Tuple[str, str]] = []

    for panel in all_panels:
        expressions = extract_prometheus_expressions(panel)
        for expr in expressions:
            if not expr.strip():
                invalid_panels.append((panel.get("title", "N/A"), "Expression is empty or whitespace"))
                continue

            metric_names = get_all_metric_names_from_expr(expr)
            for name in metric_names:
                # 1. Check against the denylist of deprecated metrics.
                if name in METRIC_NAME_DENYLIST:
                    invalid_panels.append(
                        (panel.get("title", "N/A"), f"Uses deprecated metric name: {name}")
                    )
                    continue

                # 2. Check if the metric has a known third-party prefix. If so, skip further checks.
                if any(name.startswith(prefix) for prefix in KNOWN_THIRD_PARTY_PREFIXES):
                    continue

                # 3. Handle special cases like the 'up' metric.
                if name == 'up':
                    continue

                # 4. If it's not a known third-party metric, it must have an app-specific prefix.
                if not any(name.startswith(prefix) for prefix in METRIC_PREFIX_ALLOWLIST):
                    invalid_panels.append(
                        (panel.get("title", "N/A"), f"Metric name '{name}' does not have a valid prefix.")
                    )

    if invalid_panels:
        error_message = f"Dashboard '{dashboard_path.name}' has panels with invalid metric queries:\n"
        for panel_title, reason in invalid_panels:
            error_message += f"  - Panel '{panel_title}': {reason}\n"
        pytest.fail(error_message)
