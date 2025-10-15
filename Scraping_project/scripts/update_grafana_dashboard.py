#!/usr/bin/env python3
"""Script to programmatically add Stage 4 metrics panels to Grafana dashboard."""

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def add_stage4_panels(dashboard_path: Path):
    """Add Stage 4 metrics panels to the Grafana dashboard JSON."""

    if not dashboard_path.exists():
        logger.error(f"Dashboard file not found: {dashboard_path}")
        return False

    # Load existing dashboard
    with open(dashboard_path) as f:
        dashboard = json.load(f)

    logger.info(f"Loaded dashboard: {dashboard.get('title')}")

    # Find the highest panel ID and Y position
    max_id = max(panel["id"] for panel in dashboard.get("panels", []))
    max_y = max(
        panel["gridPos"]["y"] + panel["gridPos"]["h"]
        for panel in dashboard.get("panels", [])
    )

    logger.info(f"Max panel ID: {max_id}, Max Y position: {max_y}")

    # Define new Stage 4 panels
    new_panels = [
        {
            "id": max_id + 1,
            "title": "Stage 4: HTTP Requests Rate",
            "description": "Rate of HTTP requests per second made by Stage 4 processor",
            "type": "graph",
            "gridPos": {"h": 8, "w": 12, "x": 0, "y": max_y},
            "targets": [
                {
                    "expr": "rate(stage4_http_requests_total[5m])",
                    "legendFormat": "HTTP Requests/sec",
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "unit": "reqps",
                    "custom": {
                        "lineWidth": 2,
                        "fillOpacity": 10,
                        "showPoints": "never",
                    },
                    "color": {"mode": "palette-classic"},
                }
            },
            "options": {
                "tooltip": {"mode": "multi"},
                "legend": {
                    "displayMode": "list",
                    "placement": "bottom",
                    "showLegend": True,
                },
            },
        },
        {
            "id": max_id + 2,
            "title": "Stage 4: HTTP Failure Rate by Type",
            "description": "Rate of HTTP failures broken down by error type",
            "type": "graph",
            "gridPos": {"h": 8, "w": 12, "x": 12, "y": max_y},
            "targets": [
                {
                    "expr": "rate(stage4_http_failures_total[5m])",
                    "legendFormat": "{{error_type}}",
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "unit": "reqps",
                    "custom": {
                        "lineWidth": 2,
                        "fillOpacity": 10,
                        "showPoints": "never",
                        "stacking": {"mode": "normal"},
                    },
                    "color": {"mode": "palette-classic"},
                }
            },
            "options": {
                "tooltip": {"mode": "multi"},
                "legend": {
                    "displayMode": "list",
                    "placement": "bottom",
                    "showLegend": True,
                },
            },
        },
        {
            "id": max_id + 3,
            "title": "Stage 4: Success Rate",
            "description": "Percentage of successful HTTP requests",
            "type": "gauge",
            "gridPos": {"h": 6, "w": 8, "x": 0, "y": max_y + 8},
            "targets": [
                {
                    "expr": "(rate(stage4_http_requests_total[5m]) - rate(stage4_http_failures_total[5m])) / rate(stage4_http_requests_total[5m]) * 100",
                    "legendFormat": "Success Rate",
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "unit": "percent",
                    "min": 0,
                    "max": 100,
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"value": None, "color": "red"},
                            {"value": 90, "color": "yellow"},
                            {"value": 95, "color": "green"},
                        ],
                    },
                }
            },
            "options": {"showThresholdLabels": False, "showThresholdMarkers": True},
        },
        {
            "id": max_id + 4,
            "title": "Stage 4: Total Failures by Type",
            "description": "Distribution of error types",
            "type": "piechart",
            "gridPos": {"h": 6, "w": 8, "x": 8, "y": max_y + 8},
            "targets": [
                {
                    "expr": "sum by (error_type) (stage4_http_failures_total)",
                    "legendFormat": "{{error_type}}",
                    "refId": "A",
                }
            ],
            "options": {
                "legend": {
                    "displayMode": "list",
                    "placement": "right",
                    "showLegend": True,
                },
                "pieType": "pie",
                "displayLabels": ["percent"],
            },
        },
        {
            "id": max_id + 5,
            "title": "Stage 4: Total HTTP Requests",
            "description": "Total number of HTTP requests made",
            "type": "stat",
            "gridPos": {"h": 6, "w": 8, "x": 16, "y": max_y + 8},
            "targets": [
                {
                    "expr": "stage4_http_requests_total",
                    "legendFormat": "Total Requests",
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "unit": "short",
                    "decimals": 0,
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"value": None, "color": "blue"},
                            {"value": 100, "color": "green"},
                        ],
                    },
                }
            },
            "options": {
                "graphMode": "area",
                "colorMode": "value",
                "textMode": "value_and_name",
            },
        },
    ]

    # Add new panels to dashboard
    dashboard["panels"].extend(new_panels)

    # Update version
    dashboard["version"] = dashboard.get("version", 0) + 1

    # Save updated dashboard
    backup_path = dashboard_path.with_suffix(".json.backup")
    dashboard_path.rename(backup_path)
    logger.info(f"Created backup: {backup_path}")

    with open(dashboard_path, "w") as f:
        json.dump(dashboard, f, indent=2)

    logger.info(f"✅ Added {len(new_panels)} Stage 4 panels to dashboard")
    logger.info(f"Updated dashboard saved to: {dashboard_path}")

    return True


def main():
    """Main entry point."""
    # Find dashboard file
    project_root = Path(__file__).parent.parent
    dashboard_path = (
        project_root / "monitoring" / "dashboards" / "unified_dashboard.json"
    )

    if not dashboard_path.exists():
        logger.error(f"Dashboard not found at: {dashboard_path}")
        return

    success = add_stage4_panels(dashboard_path)

    if success:
        logger.info("✅ Dashboard update complete!")
        logger.info("Restart Grafana to see the new panels")
    else:
        logger.error("❌ Dashboard update failed")


if __name__ == "__main__":
    main()
