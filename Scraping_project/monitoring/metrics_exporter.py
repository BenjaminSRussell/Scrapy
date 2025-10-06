"""Prometheus Metrics Exporter for the Scraping Pipeline.

Exports metrics about:
- Queue depths
- Processing throughput
- Error rates
- Consumer lag
- Circuit breaker status
"""

import logging
import sys
import time
from pathlib import Path

from prometheus_client import Counter, Gauge, Histogram, start_http_server

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.config import get_config
from src.common.delta_lake import get_delta_manager
from src.common.redis_manager import get_redis_manager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Define Prometheus metrics
redis_queue_length = Gauge(
    'redis_queue_length',
    'Length of Redis message queues',
    ['queue']
)

urls_processed_total = Counter(
    'urls_processed_total',
    'Total number of URLs processed',
    ['stage']
)

errors_total = Counter(
    'errors_total',
    'Total number of errors',
    ['stage', 'error_type']
)

consumer_lag_seconds = Gauge(
    'consumer_lag_seconds',
    'Consumer lag in seconds',
    ['consumer']
)

circuit_breaker_open_count = Gauge(
    'circuit_breaker_open_count',
    'Number of open circuit breakers'
)

total_urls_discovered = Gauge(
    'total_urls_discovered',
    'Total URLs discovered'
)

active_workers_count = Gauge(
    'active_workers_count',
    'Number of active workers',
    ['stage']
)

processing_time_seconds = Histogram(
    'processing_time_seconds',
    'Time spent processing items',
    ['stage'],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]
)

delta_lake_records = Gauge(
    'delta_lake_records',
    'Number of records in Delta Lake tables',
    ['table']
)

# New metrics for enhanced dashboard
urls_processed_per_second = Gauge(
    'urls_processed_per_second',
    'URLs processed per second (5-second window)',
    ['stage']
)

delta_lake_total_records = Gauge(
    'delta_lake_total_records',
    'Total number of records across all Delta Lake tables'
)

delta_lake_size_bytes = Gauge(
    'delta_lake_size_bytes',
    'Size of Delta Lake table in bytes',
    ['table']
)


class MetricsExporter:
    """Exports pipeline metrics to Prometheus."""

    def __init__(self, port: int = 9090, update_interval: int = 5):
        """Initialize exporter.

        Args:
            port: Port to expose metrics on
            update_interval: Seconds between metric updates (default: 5 for live stats)
        """
        self.port = port
        self.update_interval = update_interval

        # Initialize managers
        config = get_config()

        redis_config = config.redis_config
        self.redis = get_redis_manager(
            host=redis_config.get('host', 'localhost'),
            port=redis_config.get('port', 6379),
            db=redis_config.get('db', 0),
            password=redis_config.get('password'),
        )

        self.delta = get_delta_manager()

        # Track previous counts for rate calculation
        self.previous_counts = {}
        self.last_update_time = time.time()

        logger.info(f"Metrics exporter initialized on port {port} with {update_interval}s update interval")

    def start(self):
        """Start metrics server and update loop."""
        # Start Prometheus HTTP server
        start_http_server(self.port)
        logger.info(f"Metrics server started on http://localhost:{self.port}/metrics")

        # Run update loop
        self._update_loop()

    def _update_loop(self):
        """Continuously update metrics."""
        while True:
            try:
                self._update_queue_metrics()
                self._update_circuit_breaker_metrics()
                self._update_delta_lake_metrics()
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
            redis_queue_length.labels(queue='priority_queue').set(pq_size)

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
                'stage1_discovery',
                'stage1_errors',
                'stage2_page_analysis',
                'stage3_summaries',
                'stage4_large_docs',
                'stage4_summaries',
            ]

            total_records = 0

            for table in tables:
                try:
                    records = self.delta.read(table)
                    count = len(records) if records else 0
                    delta_lake_records.labels(table=table).set(count)
                    total_records += count

                    # Calculate table size
                    table_path = f"data/delta_lake/{table}"
                    if os.path.exists(table_path):
                        size = sum(
                            os.path.getsize(os.path.join(dirpath, filename))
                            for dirpath, dirnames, filenames in os.walk(table_path)
                            for filename in filenames
                        )
                        delta_lake_size_bytes.labels(table=table).set(size)

                    # Update total URLs discovered
                    if table == 'stage1_discovery':
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
                    'stage1_discovery': 'stage1',
                    'stage2_page_analysis': 'stage2',
                    'stage3_summaries': 'stage3',
                    'stage4_summaries': 'stage4',
                }

                for table, stage in tables_to_stages.items():
                    try:
                        records = self.delta.read(table)
                        current_count = len(records) if records else 0

                        # Get previous count
                        previous_count = self.previous_counts.get(table, current_count)

                        # Calculate rate (records per second)
                        if time_delta > 0:
                            rate = (current_count - previous_count) / time_delta
                            urls_processed_per_second.labels(stage=stage).set(max(0, rate))

                        # Update previous count
                        self.previous_counts[table] = current_count

                    except Exception as e:
                        logger.debug(f"Could not calculate throughput for {table}: {e}")

            self.last_update_time = current_time

        except Exception as e:
            logger.error(f"Error updating throughput metrics: {e}")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Prometheus Metrics Exporter for Scraping Pipeline"
    )

    parser.add_argument(
        '--port',
        type=int,
        default=9090,
        help='Port to expose metrics on (default: 9090)'
    )

    parser.add_argument(
        '--interval',
        type=int,
        default=10,
        help='Update interval in seconds (default: 10)'
    )

    args = parser.parse_args()

    exporter = MetricsExporter(port=args.port, update_interval=args.interval)
    exporter.start()


if __name__ == '__main__':
    main()
