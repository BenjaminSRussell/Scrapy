#!/usr/bin/env python
"""Simple Prometheus metrics exporter for the scraping pipeline"""
from prometheus_client import start_http_server, Gauge, Counter, Summary
import time
import redis
import sys

# Create metrics
REDIS_KEYS = Gauge('pipeline_redis_keys', 'Number of keys in Redis')
REDIS_MEMORY = Gauge('pipeline_redis_memory_bytes', 'Redis memory usage in bytes')
PIPELINE_RUNNING = Gauge('pipeline_running', 'Pipeline running status (1=running, 0=stopped)')

def collect_metrics():
    """Collect metrics from Redis and other sources"""
    try:
        r = redis.Redis(host='localhost', port=6379, decode_responses=True)

        # Get Redis metrics
        db_size = r.dbsize()
        REDIS_KEYS.set(db_size)

        info = r.info('memory')
        REDIS_MEMORY.set(info.get('used_memory', 0))

        PIPELINE_RUNNING.set(1)

    except Exception as e:
        print(f"Error collecting metrics: {e}", file=sys.stderr)
        PIPELINE_RUNNING.set(0)

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9090

    print(f"Starting Prometheus metrics exporter on port {port}")
    start_http_server(port)
    print(f"✅ Metrics available at http://localhost:{port}/metrics")

    # Collect metrics every 10 seconds
    while True:
        collect_metrics()
        time.sleep(10)
