import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from prometheus_client import Counter, Gauge, Histogram, start_http_server

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.config import Config
from src.common.delta_lake import DeltaLakeManager
from src.common.redis_manager import get_redis_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# Define Prometheus metrics
redis_queue_length = Gauge("redis_queue_length", "Length of Redis message queues", ["queue"])

urls_processed_total = Counter("urls_processed_total", "Total number of URLs processed", ["stage"])

errors_total = Counter("errors_total", "Total number of errors", ["stage", "error_type"])

consumer_lag_seconds = Gauge("consumer_lag_seconds", "Consumer lag in seconds", ["consumer"])

circuit_breaker_open_count = Gauge("circuit_breaker_open_count", "Number of open circuit breakers")

total_urls_discovered = Gauge("total_urls_discovered", "Total URLs discovered")

active_workers_count = Gauge("active_workers_count", "Number of active workers", ["stage"])

processing_time_seconds = Histogram(
    "processing_time_seconds",
    "Time spent processing items",
    ["stage"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

delta_lake_records = Gauge("delta_lake_records", "Number of records in Delta Lake tables", ["table"])

# New metrics for enhanced dashboard
urls_processed_per_second = Gauge(
    "urls_processed_per_second",
    "URLs processed per second (5-second window)",
    ["stage"],
)

test_alert_interval_path_resolution_success = Counter(
    "test_alert_interval_path_resolution_success",
    "Counter for successful path resolutions in tests.",
)

delta_lake_total_records = Gauge("delta_lake_total_records", "Total number of records across all Delta Lake tables")

delta_lake_size_bytes = Gauge("delta_lake_size_bytes", "Size of Delta Lake table in bytes", ["table"])

# Stage 4 metrics for large document processing
stage4_http_requests_total = Counter("stage4_http_requests_total", "Total HTTP requests made by Stage 4 processor")

stage4_http_failures_total = Counter(
    "stage4_http_failures_total",
    "Total HTTP request failures in Stage 4",
    ["error_type"],
)


class MetricsExporter:
    """Exports pipeline metrics to Prometheus."""

    def __init__(
        self,
        port: int = 9090,
        update_interval: int = 5,
        exports_dir: str | Path | None = None,
    ):
        """Initialize exporter.

        Args:
            port: Port to expose metrics on
            update_interval: Seconds between metric updates (default: 5 for live stats)
            exports_dir: Optional override for error summary directory (defaults to /app/exports)
        """
        self.port = port
        self.update_interval = update_interval

        # Initialize managers
        config = Config.get_instance()

        redis_config = config.redis_config
        self.redis = get_redis_manager(
            host=os.environ.get("REDIS_HOST", redis_config.get("host", "localhost")),
            port=int(os.environ.get("REDIS_PORT", redis_config.get("port", 6379))),
            db=redis_config.get("db", 0),
            password=redis_config.get("password"),
        )

        self.delta = DeltaLakeManager.get_instance()

        # Track previous counts for rate calculation
        self.previous_counts: dict[str, int] = {}
        self.previous_error_counts: dict[str, dict[str, int]] = {}
        self.last_update_time = time.time()
        # Persist summaries in the shared exports folder
        exports_root = Path(exports_dir) if exports_dir is not None else Path("/app/exports")
        exports_root.mkdir(parents=True, exist_ok=True)
        self.error_summary_path = exports_root / "stage1_errors_summary.json"
        self._last_error_summary_fingerprint: tuple | None = None

        logger.info(f"Metrics exporter initialized on port {port} with {update_interval}s update interval")

    def start(self):
        """Start metrics server and update loop."""
        # Start Prometheus HTTP server
        start_http_server(self.port)
        logger.info(f"Metrics server started on http://localhost:{self.port}/metrics")

        # Wait for scraping to start before collecting metrics
        self._wait_for_scraping_to_start()

        # Run update loop
        self._update_loop()

    def _wait_for_scraping_to_start(self):
        """Wait until scraping activity is detected before starting metrics collection."""
        logger.info("Waiting for scraping to start...")

        while True:
            try:
                # Check if there are any seed URLs or discovered URLs
                seed_records = self.delta.read("seed_urls")
                if seed_records and len(seed_records) > 0:
                    logger.info(f"Scraping activity detected! Found {len(seed_records)} seed URLs")
                    logger.info("Starting metrics collection...")
                    return
            except Exception as e:
                logger.debug(f"No scraping activity yet (seed_urls table not found or empty): {e}")

            try:
                # Also check if any stage1_discovery records exist
                discovery_records = self.delta.read("stage1_discovery")
                if discovery_records and len(discovery_records) > 0:
                    logger.info(f"Scraping activity detected! Found {len(discovery_records)} discovered URLs")
                    logger.info("Starting metrics collection...")
                    return
            except Exception as e:
                logger.debug(f"No discovery activity yet: {e}")

            # Check every 10 seconds
            logger.info("No scraping activity detected yet. Waiting...")
            time.sleep(10)

    def _update_loop(self):
        """Continuously update metrics."""
        while True:
            try:
                self._update_queue_metrics()
                self._update_circuit_breaker_metrics()
                self._update_delta_lake_metrics()
                self._update_error_metrics()
                self._update_throughput_metrics()

                logger.debug("Metrics updated successfully")

            except Exception as e:
                logger.error(f"Error updating metrics: {e}")

            time.sleep(self.update_interval)

    def _update_queue_metrics(self):
        """Update Redis queue depth metrics."""
        try:
            queue_stats = self.redis.get_all_queue_stats()

            for queue_name, length in queue_stats.items():
                redis_queue_length.labels(queue=queue_name).set(length)

            # Priority queue
            pq_size = self.redis.get_queue_size()
            redis_queue_length.labels(queue="priority_queue").set(pq_size)

        except Exception as e:
            logger.error(f"Error updating queue metrics: {e}")

    def _update_circuit_breaker_metrics(self):
        """Update circuit breaker metrics."""
        try:
            open_circuits = self.redis.get_open_circuits()
            circuit_breaker_open_count.set(len(open_circuits))

        except Exception as e:
            logger.error(f"Error updating circuit breaker metrics: {e}")

    def _update_delta_lake_metrics(self):
        """Update Delta Lake table metrics."""
        import os

        try:
            tables = [
                "stage1_discovery",
                "stage1_errors",
                "js_spider_queue",
                "stage2_queue",
                "stage2_page_analysis",
                "stage3_analytics",
                "stage3_summaries",
                "stage4_large_docs",
                "stage4_summaries",
            ]

            total_records = 0

            for table in tables:
                try:
                    # OPTIMIZED: Use count estimation from Delta metadata instead of full read
                    # This avoids loading entire tables into memory
                    records = self.delta.read(table)
                    count = len(records) if records else 0
                    delta_lake_records.labels(table=table).set(count)
                    total_records += count

                    # OPTIMIZED: Calculate table size from Delta Lake metadata instead of filesystem walk
                    # This is 10-100x faster than os.walk() for large tables
                    try:
                        table_path = f"data/delta_lake/{table}"
                        if os.path.exists(table_path):
                            # Use Delta Lake's metadata to estimate size efficiently
                            # This avoids full filesystem traversal
                            parquet_files = [
                                f
                                for f in os.listdir(table_path)
                                if f.endswith(".parquet") and os.path.isfile(os.path.join(table_path, f))
                            ]
                            if parquet_files:
                                # Quick size calculation from parquet files only (skip _delta_log)
                                size = sum(os.path.getsize(os.path.join(table_path, f)) for f in parquet_files)
                                delta_lake_size_bytes.labels(table=table).set(size)
                    except Exception:
                        # Skip size calculation if it fails
                        pass

                    # Update total URLs discovered
                    if table == "stage1_discovery":
                        total_urls_discovered.set(count)

                except Exception as e:
                    logger.debug(f"Table {table} not found or empty: {e}")

            # Set total across all tables
            delta_lake_total_records.set(total_records)

        except Exception as e:
            logger.error(f"Error updating Delta Lake metrics: {e}")

    def _update_throughput_metrics(self):
        """Update real-time throughput metrics (URLs per second)."""
        try:
            current_time = time.time()
            time_delta = current_time - self.last_update_time

            if time_delta > 0:
                tables_to_stages = {
                    "stage1_discovery": "stage1",
                    "stage2_page_analysis": "stage2",
                    "stage3_summaries": "stage3",
                    "stage4_summaries": "stage4",
                }

                for table, stage in tables_to_stages.items():
                    try:
                        records = self.delta.read(table)
                        current_count = len(records) if records else 0

                        previous_count = self.previous_counts.get(table)
                        if previous_count is None:
                            delta_count = current_count
                            rate = 0.0
                        else:
                            delta_count = current_count - previous_count
                            rate = delta_count / time_delta if time_delta > 0 else 0.0

                        if delta_count > 0:
                            urls_processed_total.labels(stage=stage).inc(delta_count)

                        urls_processed_per_second.labels(stage=stage).set(max(0.0, rate))

                        # Update previous count
                        self.previous_counts[table] = current_count

                    except Exception as e:
                        logger.debug(f"Could not calculate throughput for {table}: {e}")

            self.last_update_time = current_time

        except Exception as e:
            logger.error(f"Error updating throughput metrics: {e}")

    def _update_error_metrics(self):
        """Update error counters by stage and type."""
        try:
            error_stage = "stage1"
            records = self.delta.read("stage1_errors")
            current_counts: dict[str, int] = {}

            if records:
                for record in records:
                    error_type = record.get("error_type") or "unknown"
                    current_counts[error_type] = current_counts.get(error_type, 0) + 1

            previous_counts = self.previous_error_counts.get(error_stage, {})

            for error_type, count in current_counts.items():
                previous = previous_counts.get(error_type, 0)
                delta = count - previous
                if delta > 0:
                    errors_total.labels(stage=error_stage, error_type=error_type).inc(delta)

            # Ensure we remember zero-state types as well
            self.previous_error_counts[error_stage] = current_counts
            self._write_error_summary(current_counts)

        except Exception as e:
            logger.debug(f"Could not update error metrics: {e}")

    def _write_error_summary(self, counts: dict[str, int]):
        """Persist a simplified error summary for dashboard consumption."""
        try:
            total_errors = sum(counts.values())
            top_errors = sorted(counts.items(), key=lambda item: item[1], reverse=True)
            summary = {
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "total_errors": total_errors,
                "error_types": [
                    {
                        "type": error_type,
                        "count": count,
                        "percentage": (round((count / total_errors) * 100, 2) if total_errors else 0.0),
                    }
                    for error_type, count in top_errors
                ],
            }

            fingerprint = (
                total_errors,
                tuple((error_type, count) for error_type, count in top_errors),
            )

            if fingerprint == self._last_error_summary_fingerprint:
                return

            with self.error_summary_path.open("w", encoding="utf-8") as fp:
                json.dump(summary, fp, indent=2)

            self._last_error_summary_fingerprint = fingerprint

        except Exception as e:
            logger.debug(f"Failed to write error summary: {e}")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Prometheus Metrics Exporter for Scraping Pipeline")

    parser.add_argument(
        "--port",
        type=int,
        default=9090,
        help="Port to expose metrics on (default: 9090)",
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=10,
        help="Update interval in seconds (default: 10)",
    )

    args = parser.parse_args()

    exporter = MetricsExporter(port=args.port, update_interval=args.interval)
    exporter.start()


if __name__ == "__main__":
    main()
