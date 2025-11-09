import json
import re
from pathlib import Path

import pytest

DASHBOARD_PATHS = [
    Path(__file__).parent.parent.parent / "monitoring" / "dashboards",
    Path(__file__).parent.parent.parent / "ops" / "grafana" / "dashboards",
]
METRIC_PREFIX_ALLOWLIST = {"scrapy_", "app_", "kafka_"}
METRIC_NAME_DENYLIST = {"scrapy_pages_scraped_total", "old_request_errors_total"}

def find_dashboard_files() -> list[Path]:
    found_files: list[Path] = []
    for path in DASHBOARD_PATHS:
        if path.is_dir():
            found_files.extend(path.rglob("*.json"))
    return found_files

def extract_prometheus_expressions(panel: dict) -> list[str]:
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
    "abs",
    "absent",
    "avg_over_time",
    "ceil",
    "changes",
    "clamp_max",
    "clamp_min",
    "count_over_time",
    "days_in_month",
    "day_of_month",
    "day_of_week",
    "delta",
    "deriv",
    "exp",
    "floor",
    "histogram_quantile",
    "holt_winters",
    "hour",
    "idelta",
    "increase",
    "irate",
    "label_join",
    "label_replace",
    "ln",
    "log10",
    "log2",
    "max_over_time",
    "min_over_time",
    "minute",
    "month",
    "predict_linear",
    "quantile_over_time",
    "rate",
    "resets",
    "round",
    "scalar",
    "sort",
    "sort_desc",
    "sqrt",
    "stddev_over_time",
    "stdvar_over_time",
    "sum_over_time",
    "time",
    "timestamp",
    "vector",
    "year",
    "sum",
    "min",
    "max",
    "avg",
    "group",
    "stddev",
    "stdvar",
    "count",
    "count_values",
    "bottomk",
    "topk",
    "quantile",
    "by",
    "without",
    "on",
    "ignoring",
    "group_left",
    "group_right",
}

def get_all_metric_names_from_expr(expr: str) -> set[str]:
    potential_names = set(re.findall(r"\b([a-zA-Z_:][a-zA-Z0-9_:]+)\b", expr))

    metric_names = {name for name in potential_names if name not in PROMQL_KEYWORDS and not name.isdigit()}
    return metric_names

@pytest.mark.parametrize("dashboard_path", find_dashboard_files())
def test_grafana_dashboard_validity(dashboard_path: Path):
    if not find_dashboard_files():
        pytest.skip("No Grafana dashboards found to test.")

    try:
        with open(dashboard_path) as f:
            dashboard_json = json.load(f)
    except json.JSONDecodeError:
        pytest.fail(f"Invalid JSON in dashboard: {dashboard_path}")

    assert "panels" in dashboard_json or "rows" in dashboard_json, "Dashboard must have panels or rows"

    all_panels = dashboard_json.get("panels", [])
    for row in dashboard_json.get("rows", []):
        all_panels.extend(row.get("panels", []))

    invalid_panels: list[tuple[str, str]] = []

    for panel in all_panels:
        expressions = extract_prometheus_expressions(panel)
        for expr in expressions:
            if not expr.strip():
                invalid_panels.append((panel.get("title", "N/A"), "Expression is empty or whitespace"))
                continue

            metric_names = get_all_metric_names_from_expr(expr)
            for name in metric_names:
                if name in METRIC_NAME_DENYLIST:
                    invalid_panels.append(
                        (
                            panel.get("title", "N/A"),
                            f"Uses deprecated metric name: {name}",
                        )
                    )
                if not any(name.startswith(prefix) for prefix in METRIC_PREFIX_ALLOWLIST):
                    invalid_panels.append(
                        (
                            panel.get("title", "N/A"),
                            f"Metric name '{name}' does not have a valid prefix.",
                        )
                    )

    if invalid_panels:
        error_message = f"Dashboard '{dashboard_path.name}' has panels with invalid metric queries:\n"
        for panel_title, reason in invalid_panels:
            error_message += f"  - Panel '{panel_title}': {reason}\n"
        pytest.fail(error_message)
