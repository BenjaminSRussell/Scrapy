#!/usr/bin/env python3
"""Multi-format metrics exporter.

Exports Prometheus metrics to multiple formats for analysis and integration.
Supports: JSON, CSV, Parquet, InfluxDB Line Protocol
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("❌ requests not installed. Run: pip install requests")
    sys.exit(1)

try:
    import pandas as pd
except ImportError:
    print("❌ pandas not installed. Run: pip install pandas")
    sys.exit(1)


class MetricsExporter:
    """Export Prometheus metrics to multiple formats."""

    def __init__(self, prometheus_url="http://localhost:9091"):
        self.prometheus_url = prometheus_url

    def query_metrics(self, query):
        """Query Prometheus and return results."""
        url = f"{self.prometheus_url}/api/v1/query"
        try:
            response = requests.get(url, params={"query": query}, timeout=10)
            response.raise_for_status()
            data = response.json()

            if data["status"] != "success":
                raise ValueError(f"Query failed: {data}")

            return data["data"]["result"]
        except requests.exceptions.RequestException as e:
            print(f"⚠️  Failed to query Prometheus: {e}")
            return []

    def export_json(self, metrics, output_file):
        """Export to JSON format."""
        output_file = Path(output_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, "w") as f:
            json.dump(metrics, f, indent=2)
        print(f"✅ Exported to JSON: {output_file}")

    def export_csv(self, metrics, output_file):
        """Export to CSV format."""
        output_file = Path(output_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Flatten metrics for CSV
        rows = []
        for metric in metrics:
            row = {
                "metric_name": metric["metric"].get("__name__", "unknown"),
                "timestamp": datetime.now().isoformat(),
                "value": metric["value"][1] if len(metric["value"]) > 1 else None,
            }
            # Add all labels
            for key, value in metric["metric"].items():
                if key != "__name__":
                    row[f"label_{key}"] = value
            rows.append(row)

        df = pd.DataFrame(rows)
        df.to_csv(output_file, index=False)
        print(f"✅ Exported to CSV: {output_file} ({len(rows)} rows)")

    def export_parquet(self, metrics, output_file):
        """Export to Parquet format."""
        output_file = Path(output_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Flatten metrics
        rows = []
        for metric in metrics:
            row = {
                "metric_name": metric["metric"].get("__name__", "unknown"),
                "timestamp": datetime.now().isoformat(),
                "value": float(metric["value"][1]) if len(metric["value"]) > 1 else 0.0,
            }
            # Add all labels
            for key, value in metric["metric"].items():
                if key != "__name__":
                    row[f"label_{key}"] = value
            rows.append(row)

        df = pd.DataFrame(rows)
        df.to_parquet(output_file, index=False)
        print(f"✅ Exported to Parquet: {output_file} ({len(rows)} rows)")

    def export_influxdb_line_protocol(self, metrics, output_file):
        """Export to InfluxDB line protocol format."""
        output_file = Path(output_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        lines = []
        timestamp_ns = int(datetime.now().timestamp() * 1e9)

        for metric in metrics:
            metric_name = metric["metric"].get("__name__", "unknown")
            labels = ",".join(
                [f"{k}={v}" for k, v in metric["metric"].items() if k != "__name__"]
            )
            value = metric["value"][1] if len(metric["value"]) > 1 else 0

            if labels:
                line = f"{metric_name},{labels} value={value} {timestamp_ns}"
            else:
                line = f"{metric_name} value={value} {timestamp_ns}"

            lines.append(line)

        with open(output_file, "w") as f:
            f.write("\n".join(lines))

        print(f"✅ Exported to InfluxDB format: {output_file} ({len(lines)} metrics)")


def main():
    parser = argparse.ArgumentParser(
        description="Export Prometheus metrics to multiple formats"
    )
    parser.add_argument(
        "--format",
        choices=["json", "csv", "parquet", "influx", "all"],
        default="all",
        help="Export format",
    )
    parser.add_argument(
        "--output-dir",
        default="exports/metrics",
        help="Output directory for exported files",
    )
    parser.add_argument(
        "--prometheus-url",
        default="http://localhost:9091",
        help="Prometheus server URL",
    )
    args = parser.parse_args()

    exporter = MetricsExporter(prometheus_url=args.prometheus_url)

    # Define key metrics to export
    metrics_queries = {
        "scraping_rate": "rate(scrapy_items_scraped_total[5m])",
        "consumer_lag": "kafka_consumer_lag",
        "drop_rate": "rate(scrapy_items_dropped_total[5m])",
        "processing_rate": "rate(kafka_messages_processed_total[5m])",
        "items_scraped": "scrapy_items_scraped_total",
        "items_dropped": "scrapy_items_dropped_total",
        "delta_write_latency": "delta_batch_write_seconds",
    }

    print("=" * 80)
    print("METRICS EXPORT - Multi-Format")
    print("=" * 80)
    print(f"Prometheus URL: {args.prometheus_url}")
    print(f"Output directory: {args.output_dir}")
    print(f"Export format(s): {args.format}")
    print("")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    exported_count = 0

    for name, query in metrics_queries.items():
        print(f"\nExporting: {name}")
        print(f"  Query: {query}")

        metrics = exporter.query_metrics(query)

        if not metrics:
            print("  ⚠️  No data returned")
            continue

        print(f"  Found {len(metrics)} metric(s)")

        # Export in requested format(s)
        if args.format in ["json", "all"]:
            exporter.export_json(metrics, f"{args.output_dir}/{name}_{timestamp}.json")
            exported_count += 1

        if args.format in ["csv", "all"]:
            exporter.export_csv(metrics, f"{args.output_dir}/{name}_{timestamp}.csv")
            exported_count += 1

        if args.format in ["parquet", "all"]:
            exporter.export_parquet(
                metrics, f"{args.output_dir}/{name}_{timestamp}.parquet"
            )
            exported_count += 1

        if args.format in ["influx", "all"]:
            exporter.export_influxdb_line_protocol(
                metrics, f"{args.output_dir}/{name}_{timestamp}.influx"
            )
            exported_count += 1

    print("\n" + "=" * 80)
    print(f"✅ Export complete: {exported_count} file(s) created")
    print(f"📁 Location: {args.output_dir}")
    print("=" * 80)


if __name__ == "__main__":
    main()
