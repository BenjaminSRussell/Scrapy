#!/usr/bin/env python3
"""
Parses src/scrapy_prometheus.py to extract and list all defined Prometheus metric names.
"""

import re
import sys
from pathlib import Path

def extract_metric_names():
    """
    Reads the scrapy_prometheus.py file and extracts metric names.
    """
    try:
        project_root = Path(__file__).resolve().parent.parent.parent
        prometheus_file = project_root / "src" / "scrapy_prometheus.py"
        content = prometheus_file.read_text()

        # Regex to find metric names in Counter, Gauge, Histogram definitions
        pattern = re.compile(
            r"(?:Counter|Gauge|Histogram)\(\s*['\"](?P<name>[a-zA-Z_][a-zA-Z0-9_]*)['\"]"
        )

        metric_names = sorted(list(set(pattern.findall(content))))

        return metric_names
    except FileNotFoundError:
        print("Error: scrapy_prometheus.py not found.", file=sys.stderr)
        return []

def main():
    """
    Main function to extract and print metric names.
    """
    metric_names = extract_metric_names()

    if not metric_names:
        print("No metric names found.", file=sys.stderr)
        sys.exit(1)

    print("Canonical Prometheus Metric Names:")
    for name in metric_names:
        print(f"- {name}")

if __name__ == "__main__":
    main()
