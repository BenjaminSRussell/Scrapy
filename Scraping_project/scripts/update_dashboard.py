#!/usr/bin/env python3
"""Update Grafana Dashboard Script

This script:
1. Fixes the Kafka Consumer Lag panel metric names
2. Adds three new panels for offsite link tracking
3. Renames the backup file to the active dashboard
"""

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def fix_kafka_panel(dashboard):
    """Fix the Kafka Consumer Lag panel metric names."""
    logger.info("Fixing Kafka Consumer Lag panel...")

    for panel in dashboard.get('panels', []):
        if panel.get('title') == 'Kafka Consumer Lag':
            logger.info(f"  Found Kafka Consumer Lag panel (ID: {panel['id']})")

            # Fix the metric names in targets
            for target in panel.get('targets', []):
                old_expr = target.get('expr', '')

                # Replace incorrect metric names with correct one
                if 'consumer_lag_seconds' in old_expr:
                    target['expr'] = old_expr.replace('consumer_lag_seconds', 'kafka_consumer_records_lag')
                    logger.info(f"    Fixed: {old_expr} -> {target['expr']}")
                elif old_expr == 'kafka_consumer_lag':
                    target['expr'] = 'kafka_consumer_records_lag'
                    target['legendFormat'] = '{{consumer_group}}-{{topic}}-p{{partition}}'
                    logger.info(f"    Fixed: {old_expr} -> kafka_consumer_records_lag")

            # Update description
            panel['description'] = "Consumer lag in records for Kafka consumers (correct metric: kafka_consumer_records_lag)"
            logger.info("  ✅ Kafka Consumer Lag panel fixed")
            return True

    logger.warning("  ⚠️  Kafka Consumer Lag panel not found")
    return False


def add_offsite_panels(dashboard):
    """Add three new panels for offsite link tracking."""
    logger.info("Adding new offsite link panels...")

    panels = dashboard.get('panels', [])

    # Find max panel ID and Y position
    max_id = max((p['id'] for p in panels), default=0)
    max_y = max((p['gridPos']['y'] + p['gridPos']['h'] for p in panels), default=0)

    logger.info(f"  Max panel ID: {max_id}, Max Y position: {max_y}")

    # Panel 1: Off-site Links Found Rate (Graph)
    panel_offsite_rate = {
        "id": max_id + 1,
        "title": "Off-site Links Found Rate",
        "description": "Rate of external/offsite links discovered per second",
        "type": "timeseries",
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 0,
            "y": max_y
        },
        "targets": [
            {
                "expr": "rate(scrapy_offsite_links_found_total[5m])",
                "legendFormat": "{{spider}} - Offsite Links/sec",
                "refId": "A"
            }
        ],
        "fieldConfig": {
            "defaults": {
                "unit": "reqps",
                "custom": {
                    "drawStyle": "line",
                    "lineInterpolation": "smooth",
                    "fillOpacity": 10,
                    "showPoints": "never"
                }
            }
        },
        "options": {
            "tooltip": {
                "mode": "multi"
            },
            "legend": {
                "displayMode": "table",
                "placement": "bottom",
                "calcs": ["lastNotNull", "mean"]
            }
        }
    }

    # Panel 2: Total Off-site Candidates (Stat)
    panel_offsite_total = {
        "id": max_id + 2,
        "title": "Total Off-site Candidates",
        "description": "Total number of offsite URLs saved to Delta Lake",
        "type": "stat",
        "gridPos": {
            "h": 8,
            "w": 6,
            "x": 12,
            "y": max_y
        },
        "targets": [
            {
                "expr": "scrapy_offsite_candidates_saved_total",
                "legendFormat": "Saved",
                "refId": "A"
            }
        ],
        "options": {
            "graphMode": "area",
            "colorMode": "value",
            "textMode": "value_and_name",
            "reduceOptions": {
                "values": False,
                "calcs": ["lastNotNull"]
            }
        },
        "fieldConfig": {
            "defaults": {
                "unit": "short",
                "decimals": 0,
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {
                            "value": None,
                            "color": "blue"
                        },
                        {
                            "value": 100,
                            "color": "green"
                        },
                        {
                            "value": 1000,
                            "color": "yellow"
                        }
                    ]
                }
            }
        }
    }

    # Panel 3: Scraping Speed Comparison (Graph with onsite vs offsite)
    panel_speed_comparison = {
        "id": max_id + 3,
        "title": "Scraping Speed Comparison",
        "description": "Comparison of onsite crawling speed vs offsite link discovery",
        "type": "timeseries",
        "gridPos": {
            "h": 8,
            "w": 6,
            "x": 18,
            "y": max_y
        },
        "targets": [
            {
                "expr": "rate(scrapy_items_scraped_total{spider=\"scout\"}[5m])",
                "legendFormat": "Onsite Pages Scraped/sec",
                "refId": "A"
            },
            {
                "expr": "rate(scrapy_offsite_links_found_total{spider=\"scout\"}[5m])",
                "legendFormat": "Offsite Links Found/sec",
                "refId": "B"
            }
        ],
        "fieldConfig": {
            "defaults": {
                "unit": "reqps",
                "custom": {
                    "drawStyle": "line",
                    "lineInterpolation": "smooth",
                    "fillOpacity": 20,
                    "showPoints": "never"
                }
            }
        },
        "options": {
            "tooltip": {
                "mode": "multi"
            },
            "legend": {
                "displayMode": "list",
                "placement": "bottom"
            }
        }
    }

    # Add panels to dashboard
    panels.extend([panel_offsite_rate, panel_offsite_total, panel_speed_comparison])

    logger.info(f"  ✅ Added 3 new panels (IDs: {max_id + 1}, {max_id + 2}, {max_id + 3})")


def main():
    """Main execution."""
    dashboard_path = Path(__file__).parent.parent / 'monitoring' / 'dashboards' / 'unified_dashboard.json.backup'

    if not dashboard_path.exists():
        logger.error(f"❌ Dashboard file not found: {dashboard_path}")
        return 1

    logger.info(f"Loading dashboard: {dashboard_path}")

    # Load dashboard
    with open(dashboard_path) as f:
        dashboard = json.load(f)

    logger.info(f"  Dashboard: {dashboard.get('title')}")
    logger.info(f"  Version: {dashboard.get('version')}")
    logger.info(f"  Panels: {len(dashboard.get('panels', []))}")

    # Fix Kafka panel
    fix_kafka_panel(dashboard)

    # Add new panels
    add_offsite_panels(dashboard)

    # Increment version
    dashboard['version'] = dashboard.get('version', 1) + 1

    # Create backup of backup (just in case)
    backup_backup_path = dashboard_path.with_suffix('.json.backup.backup')
    logger.info(f"Creating safety backup: {backup_backup_path.name}")
    with open(backup_backup_path, 'w') as f:
        json.dump(dashboard, f, indent=2)

    # Write updated dashboard to backup file
    logger.info(f"Writing updated dashboard: {dashboard_path}")
    with open(dashboard_path, 'w') as f:
        json.dump(dashboard, f, indent=2)

    # Rename to active dashboard
    active_path = dashboard_path.parent / 'unified_dashboard.json'
    logger.info(f"Activating dashboard: {active_path}")
    with open(active_path, 'w') as f:
        json.dump(dashboard, f, indent=2)

    logger.info("=" * 70)
    logger.info("✅ Dashboard update complete!")
    logger.info("   - Fixed Kafka Consumer Lag panel")
    logger.info("   - Added 3 new offsite tracking panels")
    logger.info(f"   - Updated version to {dashboard['version']}")
    logger.info(f"   - Total panels: {len(dashboard['panels'])}")
    logger.info("=" * 70)

    return 0


if __name__ == '__main__':
    exit(main())
