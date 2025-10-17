import json
import logging
import os
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.config import Config
from src.common.delta_lake import DeltaLakeManager
from src.common.redis_manager import get_redis_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class StatsDClient:
    """UDP-based StatsD client for fire-and-forget metrics (no acknowledgment required)."""

    def __init__(self, host: str = "localhost", port: int = 8125):
        """Initialize StatsD UDP client.

        Args:
            host: StatsD server hostname
            port: StatsD server port (default 8125)
        """
        self.host = host
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        logger.info(f"StatsD client initialized for {host}:{port} (UDP)")

    def _send(self, metric: str):
        """Send metric via UDP (fire-and-forget, no response expected)."""
        try:
            self.sock.sendto(metric.encode("utf-8"), (self.host, self.port))
        except Exception as e:
            logger.debug(f"Failed to send metric via UDP: {e}")

    def gauge(self, name: str, value: float, tags: dict[str, str] | None = None):
        """Send gauge metric."""
        tag_str = self._format_tags(tags) if tags else ""
        metric = f"{name}:{value}|g{tag_str}"
        self._send(metric)

    def counter(self, name: str, value: int = 1, tags: dict[str, str] | None = None):
        """Send counter increment."""
        tag_str = self._format_tags(tags) if tags else ""
        metric = f"{name}:{value}|c{tag_str}"
        self._send(metric)

    def timing(self, name: str, value_ms: float, tags: dict[str, str] | None = None):
        """Send timing metric."""
        tag_str = self._format_tags(tags) if tags else ""
        metric = f"{name}:{value_ms}|ms{tag_str}"
        self._send(metric)

    def _format_tags(self, tags: dict[str, str]) -> str:
        """Format tags for StatsD (DogStatsD format: |#tag1:val1,tag2:val2)."""
        if not tags:
            return ""
        tag_list = [f"{k}:{v}" for k, v in tags.items()]
        return f"|#{','.join(tag_list)}"


class MetricsExporter:
    """Exports pipeline metrics via UDP to StatsD (fire-and-forget, no acknowledgment)."""

    def __init__(
        self,
        statsd_host: str = "localhost",
        statsd_port: int = 8125,
        update_interval: int = 5,
        exports_dir: str | Path | None = None,
    ):
        """Initialize exporter.

        Args:
            statsd_host: StatsD server hostname
            statsd_port: StatsD server port (default 8125)
            update_interval: Seconds between metric updates (default: 5 for live stats)
            exports_dir: Optional override for error summary directory (defaults to /app/exports)
        """
        self.update_interval = update_interval

        # Initialize UDP StatsD client (no acknowledgment/response required)
        self.statsd = StatsDClient(host=statsd_host, port=statsd_port)

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

        # Track delta tables so we can expose baseline metrics even before data arrives
        self._tracked_tables: list[str] = [
            "seed_urls",
            "uconn_urls",
            "stage1_discovery",
            "stage1_errors",
            "stage1_offsite_candidates",
            "js_spider_queue",
            "stage2_queue",
            "stage2_page_analysis",
            "stage3_analytics",
            "stage3_summaries",
            "stage4_large_docs",
            "stage4_summaries",
        ]

        self._throughput_table_to_stage: dict[str, str] = {
            "stage1_discovery": "stage1",
            "stage2_page_analysis": "stage2",
            "stage3_summaries": "stage3",
            "stage4_summaries": "stage4",
        }
        self._tracked_stages: list[str] = sorted(set(self._throughput_table_to_stage.values()))

        # Redis queues we want to monitor. Pre-register so Grafana panels don't show "No data".
        message_queues_cfg = getattr(config, "message_queue_config", {}) or {}
        queue_names: set[str] = {"priority_queue"}
        for value in message_queues_cfg.values():
            if isinstance(value, str):
                queue_names.add(value)
            elif isinstance(value, (list, tuple, set)):
                queue_names.update({item for item in value if isinstance(item, str)})
        self._tracked_queues = sorted(queue_names)

        # Track previous counts for rate calculation
        self.previous_counts: dict[str, int] = {}
        self.previous_error_counts: dict[str, dict[str, int]] = {}
        self.last_update_time = time.time()
        # Persist summaries in the shared exports folder
        exports_root = Path(exports_dir) if exports_dir is not None else Path("/app/exports")
        exports_root.mkdir(parents=True, exist_ok=True)
        self.error_summary_path = exports_root / "stage1_errors_summary.json"
        self._last_error_summary_fingerprint: tuple | None = None

        # Seed default zero values so dashboards have immediate data
        self._initialize_metrics()

        logger.info(f"UDP metrics exporter initialized (StatsD: {statsd_host}:{statsd_port}, interval: {update_interval}s)")

    def start(self):
        """Start metrics update loop (UDP - no server needed)."""
        logger.info("Starting UDP metrics exporter (fire-and-forget mode)")

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
        """Update Redis queue depth metrics via UDP."""
        try:
            queue_stats = self.redis.get_all_queue_stats()
            seen: set[str] = set()

            for queue_name, length in queue_stats.items():
                self.statsd.gauge("redis.queue.length", length, {"queue": queue_name})
                seen.add(queue_name)

            # Priority queue
            pq_size = self.redis.get_queue_size()
            self.statsd.gauge("redis.queue.length", pq_size, {"queue": "priority_queue"})
            seen.add("priority_queue")

            # Ensure tracked queues that weren't returned still emit zeros
            for queue_name in self._tracked_queues:
                if queue_name not in seen:
                    self.statsd.gauge("redis.queue.length", 0, {"queue": queue_name})

        except Exception as e:
            logger.error(f"Error updating queue metrics: {e}")

    def _update_circuit_breaker_metrics(self):
        """Update circuit breaker metrics via UDP."""
        try:
            open_circuits = self.redis.get_open_circuits()
            self.statsd.gauge("circuit_breaker.open_count", len(open_circuits))

        except Exception as e:
            logger.error(f"Error updating circuit breaker metrics: {e}")

    def _update_delta_lake_metrics(self):
        """Update Delta Lake table metrics via UDP."""
        try:
            total_records = 0

            for table in self._tracked_tables:
                try:
                    # Get record count
                    records = self.delta.read(table)
                    count = len(records) if records else 0
                    total_records += count

                    # Send record count gauge
                    self.statsd.gauge("delta_lake.records", count, {"table": table})

                    # Send table size gauge
                    try:
                        size = self.delta.get_table_size(table)
                        self.statsd.gauge("delta_lake.size_bytes", size, {"table": table})
                    except Exception:
                        pass

                    # Update specific metrics for key tables
                    if table == "stage1_discovery":
                        self.statsd.gauge("urls.discovered.total", count)
                    elif table == "uconn_urls":
                        self.statsd.gauge("urls.uconn.total", count)
                    elif table == "seed_urls":
                        self.statsd.gauge("urls.seed.total", count)

                except Exception as e:
                    logger.debug(f"Could not read table {table}: {e}")

            # Set total across all tables
            self.statsd.gauge("delta_lake.total_records", total_records)

        except Exception as e:
            logger.error(f"Error updating Delta Lake metrics: {e}")

    def _update_throughput_metrics(self):
        """Update real-time throughput metrics (URLs per second) via UDP."""
        try:
            current_time = time.time()
            time_delta = current_time - self.last_update_time

            if time_delta > 0:
                for table, stage in self._throughput_table_to_stage.items():
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
                            self.statsd.counter("urls.processed.total", delta_count, {"stage": stage})

                        self.statsd.gauge("urls.processed.per_second", max(0.0, rate), {"stage": stage})

                        # Update previous count
                        self.previous_counts[table] = current_count

                    except Exception as e:
                        logger.debug(f"Could not calculate throughput for {table}: {e}")

            self.last_update_time = current_time

        except Exception as e:
            logger.error(f"Error updating throughput metrics: {e}")

    def _update_error_metrics(self):
        """Update error counters by stage and type via UDP."""
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
                    self.statsd.counter("errors.total", delta, {"stage": error_stage, "error_type": error_type})

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

    def _initialize_metrics(self):
        """Prime metrics with zeros via UDP so dashboards render immediately."""
        try:
            for queue_name in self._tracked_queues:
                self.statsd.gauge("redis.queue.length", 0, {"queue": queue_name})

            for table in self._tracked_tables:
                self.statsd.gauge("delta_lake.records", 0, {"table": table})
                self.statsd.gauge("delta_lake.size_bytes", 0, {"table": table})

            self.statsd.gauge("delta_lake.total_records", 0)
            self.statsd.gauge("urls.seed.total", 0)
            self.statsd.gauge("urls.uconn.total", 0)
            self.statsd.gauge("urls.discovered.total", 0)
            self.statsd.gauge("circuit_breaker.open_count", 0)

            for stage in self._tracked_stages:
                self.statsd.gauge("urls.processed.per_second", 0.0, {"stage": stage})
                self.statsd.counter("urls.processed.total", 0, {"stage": stage})
                self.statsd.counter("errors.total", 0, {"stage": stage, "error_type": "none"})
                self.statsd.gauge("workers.active", 0, {"stage": stage})

            self.statsd.counter("stage4.http.requests.total", 0)
            self.statsd.counter("stage4.http.failures.total", 0, {"error_type": "unknown"})

        except Exception as e:
            logger.debug(f"Failed to initialize baseline metrics: {e}")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="UDP StatsD Metrics Exporter for Scraping Pipeline")

    parser.add_argument(
        "--statsd-host",
        type=str,
        default=os.environ.get("STATSD_HOST", "localhost"),
        help="StatsD server hostname (default: localhost)",
    )

    parser.add_argument(
        "--statsd-port",
        type=int,
        default=int(os.environ.get("STATSD_PORT", "8125")),
        help="StatsD server port (default: 8125)",
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        help="Update interval in seconds (default: 5)",
    )

    args = parser.parse_args()

    exporter = MetricsExporter(
        statsd_host=args.statsd_host,
        statsd_port=args.statsd_port,
        update_interval=args.interval,
    )
    exporter.start()


if __name__ == "__main__":
    main()
