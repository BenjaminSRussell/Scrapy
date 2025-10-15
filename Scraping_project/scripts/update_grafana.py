#!/usr/bin/env python3
"""
A unified script for updating Grafana dashboards.
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def load_dashboard(dashboard_path: Path) -> dict[str, Any]:
    """Loads a dashboard from a JSON file."""
    logger.info(f"Loading dashboard from: {dashboard_path}")
    if not dashboard_path.exists():
        raise FileNotFoundError(f"Dashboard file not found: {dashboard_path}")
    with open(dashboard_path) as f:
        return json.load(f)


def save_dashboard(dashboard: dict[str, Any], dashboard_path: Path) -> None:
    """Saves a dashboard to a JSON file."""
    logger.info(f"Saving dashboard to: {dashboard_path}")
    dashboard["version"] = dashboard.get("version", 1) + 1
    with open(dashboard_path, "w") as f:
        json.dump(dashboard, f, indent=2)
    logger.info(
        f"  ✅ Dashboard saved successfully. New version: {dashboard['version']}"
    )


def find_panel(
    dashboard: dict[str, Any],
    panel_title: str | None = None,
    panel_id: int | None = None,
) -> dict[str, Any] | None:
    """Finds a panel by title or ID."""
    for panel in dashboard.get("panels", []):
        if panel_title is not None and panel.get("title") == panel_title:
            return panel
        if panel_id is not None and panel.get("id") == panel_id:
            return panel
    return None


def add_panels(dashboard: dict[str, Any], panel_specs: list[dict[str, Any]]) -> None:
    """Adds new panels to the dashboard."""
    logger.info(f"Adding {len(panel_specs)} new panel(s)...")
    panels = dashboard.get("panels", [])
    max_id = max((p["id"] for p in panels), default=0) if panels else 0
    base_y = (
        max((p["gridPos"]["y"] + p["gridPos"]["h"] for p in panels), default=0)
        if panels
        else 0
    )

    for spec in panel_specs:
        max_id += 1
        spec["id"] = max_id

        grid_pos = spec.get("gridPos", {})
        # Allow for relative vertical placement in the spec
        y_offset = grid_pos.pop("y_offset", 0)
        grid_pos["y"] = base_y + y_offset
        spec["gridPos"] = grid_pos

        panels.append(spec)
        logger.info(
            f"  ✅ Panel '{spec.get('title', 'N/A')}' added with ID: {spec['id']}"
        )


def modify_panel(dashboard: dict[str, Any], panel_spec: dict[str, Any]) -> None:
    """Modifies an existing panel."""
    logger.info("Modifying panel...")
    panel_title = panel_spec.get("title")
    panel_id = panel_spec.get("id")

    panel_to_modify = find_panel(
        dashboard,
        panel_title=panel_title if isinstance(panel_title, str) else None,
        panel_id=panel_id if isinstance(panel_id, int) else None,
    )
    if not panel_to_modify:
        logger.error(
            f"  ❌ Panel with title '{panel_title}' or ID '{panel_id}' not found."
        )
        return

    # Update the panel with the new spec
    panel_to_modify.update(panel_spec)
    logger.info(
        f"  ✅ Panel '{panel_to_modify.get('title')}' (ID: {panel_to_modify.get('id')}) modified."
    )


def delete_panel(dashboard: dict[str, Any], panel_spec: dict[str, Any]) -> None:
    """Deletes a panel from the dashboard."""
    logger.info("Deleting panel...")
    panel_title = panel_spec.get("title")
    panel_id = panel_spec.get("id")

    panel_to_delete = find_panel(
        dashboard,
        panel_title=panel_title if isinstance(panel_title, str) else None,
        panel_id=panel_id if isinstance(panel_id, int) else None,
    )
    if not panel_to_delete:
        logger.error(
            f"  ❌ Panel with title '{panel_title}' or ID '{panel_id}' not found."
        )
        return

    dashboard["panels"].remove(panel_to_delete)
    logger.info(
        f"  ✅ Panel '{panel_to_delete.get('title')}' (ID: {panel_to_delete.get('id')}) deleted."
    )


def main():
    """Main execution."""
    parser = argparse.ArgumentParser(description="Update a Grafana dashboard.")
    parser.add_argument(
        "--dashboard", type=Path, required=True, help="Path to the dashboard JSON file."
    )
    parser.add_argument(
        "--op",
        required=True,
        choices=["add_panels", "modify_panel", "delete_panel"],
        help="Operation to perform.",
    )
    parser.add_argument(
        "--panel-spec",
        type=Path,
        required=True,
        help="Path to the JSON file with the panel specification.",
    )
    args = parser.parse_args()

    try:
        dashboard = load_dashboard(args.dashboard)

        with open(args.panel_spec) as f:
            panel_spec = json.load(f)

        if args.op == "add_panels":
            # Handle both single panel dict and list of panels
            specs = panel_spec if isinstance(panel_spec, list) else [panel_spec]
            add_panels(dashboard, specs)
        elif args.op == "modify_panel":
            modify_panel(dashboard, panel_spec)
        elif args.op == "delete_panel":
            delete_panel(dashboard, panel_spec)

        save_dashboard(dashboard, args.dashboard)

    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"  ❌ Error: {e}")
        exit(1)


if __name__ == "__main__":
    main()
