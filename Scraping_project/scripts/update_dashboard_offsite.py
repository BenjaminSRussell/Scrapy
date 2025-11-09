#!/usr/bin/env python3

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DASHBOARD_PATH = PROJECT_ROOT / "monitoring" / "dashboards" / "unified_dashboard.json.backup"

def add_offsite_panels(dashboard_path: Path):

    print(f"Loading dashboard from: {dashboard_path}")

    with open(dashboard_path) as f:
        dashboard = json.load(f)

    print(f"Dashboard title: {dashboard.get('title')}")

    max_id = max(panel["id"] for panel in dashboard["panels"])
    max_y = max(panel["gridPos"]["y"] + panel["gridPos"]["h"] for panel in dashboard["panels"])

    print(f"Max panel ID: {max_id}")
    print(f"Max Y position: {max_y}")

    new_panels = [
        {
            "id": max_id + 1,
            "title": "Off-site Links Found Rate",
            "description": "Rate of external (off-site) links discovered per second",
            "type": "timeseries",
            "gridPos": {"h": 8, "w": 8, "x": 0, "y": max_y},
            "targets": [
                {
                    "expr": "rate(scrapy_offsite_links_found_total[1m])",
                    "legendFormat": "{{spider}} - Offsite Links/sec",
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "unit": "short",
                    "color": {"mode": "palette-classic"},
                    "custom": {
                        "lineWidth": 2,
                        "fillOpacity": 10,
                        "drawStyle": "line",
                        "spanNulls": False,
                        "axisPlacement": "auto",
                    },
                }
            },
            "options": {
                "legend": {"displayMode": "list", "placement": "bottom"},
                "tooltip": {"mode": "multi"},
            },
        },
        {
            "id": max_id + 2,
            "title": "Total Off-site Candidates",
            "description": "Total number of external URLs saved to Delta Lake for future classification",
            "type": "stat",
            "gridPos": {"h": 8, "w": 8, "x": 8, "y": max_y},
            "targets": [
                {
                    "expr": "scrapy_offsite_candidates_saved_total",
                    "legendFormat": "{{spider}} - Saved Candidates",
                    "refId": "A",
                }
            ],
            "fieldConfig": {
                "defaults": {
                    "unit": "short",
                    "decimals": 0,
                    "color": {"mode": "thresholds"},
                    "thresholds": {
                        "mode": "absolute",
                        "steps": [
                            {"value": None, "color": "blue"},
                            {"value": 100, "color": "green"},
                            {"value": 1000, "color": "yellow"},
                            {"value": 10000, "color": "orange"},
                        ],
                    },
                }
            },
            "options": {
                "graphMode": "area",
                "colorMode": "value",
                "textMode": "value_and_name",
                "reduceOptions": {"values": False, "calcs": ["lastNotNull"]},
            },
        },
        {
            "id": max_id + 3,
            "title": "Scraping Speed Comparison",
            "description": "Comparison of internal vs external link discovery rates",
            "type": "timeseries",
            "gridPos": {"h": 8, "w": 8, "x": 16, "y": max_y},
            "targets": [
                {
                    "expr": 'rate(scrapy_items_scraped_total{spider="scout"}[1m])',
                    "legendFormat": "Internal URLs/sec",
                    "refId": "A",
                },
                {
                    "expr": 'rate(scrapy_offsite_links_found_total{spider="scout"}[1m])',
                    "legendFormat": "External URLs/sec",
                    "refId": "B",
                },
                {
                    "expr": 'rate(scrapy_items_scraped_total{spider="scout"}[1m]) + rate(scrapy_offsite_links_found_total{spider="scout"}[1m])',
                    "legendFormat": "Total URLs/sec",
                    "refId": "C",
                },
            ],
            "fieldConfig": {
                "defaults": {
                    "unit": "short",
                    "color": {"mode": "palette-classic"},
                    "custom": {
                        "lineWidth": 2,
                        "fillOpacity": 10,
                        "drawStyle": "line",
                        "spanNulls": False,
                        "axisPlacement": "auto",
                    },
                },
                "overrides": [
                    {
                        "matcher": {"id": "byName", "options": "Total URLs/sec"},
                        "properties": [
                            {"id": "custom.lineWidth", "value": 3},
                            {
                                "id": "custom.lineStyle",
                                "value": {"dash": [10, 10], "fill": "dash"},
                            },
                        ],
                    }
                ],
            },
            "options": {
                "legend": {"displayMode": "list", "placement": "bottom"},
                "tooltip": {"mode": "multi"},
            },
        },
    ]

    dashboard["panels"].extend(new_panels)

    dashboard["version"] = dashboard.get("version", 0) + 1

    print(f"\n✅ Added {len(new_panels)} new panels:")
    for panel in new_panels:
        print(f"  - Panel {panel['id']}: {panel['title']}")

    print(f"\nSaving dashboard to: {dashboard_path}")
    with open(dashboard_path, "w") as f:
        json.dump(dashboard, f, indent=2)

    print("✅ Dashboard updated successfully!")
    print(f"\nNew panels positioned at Y={max_y}")
    print("Restart Grafana or reload the dashboard to see changes.")

def main():
    if not DASHBOARD_PATH.exists():
        print(f"❌ Dashboard file not found: {DASHBOARD_PATH}")
        sys.exit(1)

    print("=" * 70)
    print("Grafana Dashboard Update - Offsite Link Panels")
    print("=" * 70 + "\n")

    try:
        add_offsite_panels(DASHBOARD_PATH)

        print("\n" + "=" * 70)
        print("✅ Dashboard update complete!")
        print("=" * 70)

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
