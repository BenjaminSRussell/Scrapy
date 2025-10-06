"""Prometheus Metrics Exporter for the Scraping Pipeline.

Exports metrics about:
- Queue depths
- Processing throughput
- Error rates
- Consumer lag
- Circuit breaker status
"""

import logging
import time
from pathlib import Path
from typing import Dict

from prometheus_client import Gauge, Counter, Histogram, start_http_server

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.config import get_config
from src.common.redis_manager import get_redis_manager
from src.common.delta_lake import get_delta_manager

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


class MetricsExporter:
    """Exports pipeline metrics to Prometheus."""

    def __init__(self, port: int = 9090, update_interval: int = 10):
        """Initialize exporter.

        Args:
            port: Port to expose metrics on
            update_interval: Seconds between metric updates
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

        logger.info(f"Metrics exporter initialized on port {port}")

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
        try:
            tables = [
                'stage1_discovery',
                'stage2_page_analysis',
                'stage3_summaries',
                'stage4_summaries',
            ]

            for table in tables:
                try:
                    records = self.delta.read(table)
                    count = len(records) if records else 0
                    delta_lake_records.labels(table=table).set(count)

                    # Update total URLs discovered
                    if table == 'stage1_discovery':
                        total_urls_discovered.set(count)

                except Exception as e:
                    logger.debug(f"Table {table} not found or empty: {e}")

        except Exception as e:
            logger.error(f"Error updating Delta Lake metrics: {e}")


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
