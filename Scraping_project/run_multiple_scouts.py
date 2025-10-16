#!/usr/bin/env python
"""
Run multiple concurrent scout spider instances for maximum throughput
"""

import logging
import multiprocessing as mp
import os

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def run_spider_instance(instance_id: int):
    """Run a single spider instance in a separate process.

    Args:
        instance_id: Unique identifier for this spider instance
    """
    logger.info(f"Starting scout spider instance {instance_id}")

    # Get settings and customize per instance
    settings = get_project_settings()

    # Each instance gets a unique Prometheus port
    settings.set("PROMETHEUS_PORT", 9410 + instance_id)
    settings.set("PROMETHEUS_ENABLED", True)

    # Create process for this instance
    process = CrawlerProcess(settings)

    # Crawl the scout spider
    process.crawl("scout")

    # Start the process (this blocks until finished)
    logger.info(f"Scout instance {instance_id} starting...")
    process.start()


def main():
    """Run multiple scout spider instances in parallel."""
    # Get number of instances from environment or default to CPU count
    num_instances = int(os.getenv("SCOUT_INSTANCES", mp.cpu_count()))

    logger.info(f"Launching {num_instances} concurrent scout spider instances")
    logger.info("Each instance will use extreme concurrency settings:")
    logger.info("  - CONCURRENT_REQUESTS: 1024")
    logger.info("  - CONCURRENT_REQUESTS_PER_DOMAIN: 512")
    logger.info(f"  - Total theoretical max: {num_instances * 1024} concurrent requests")

    # Create processes for each spider instance
    processes = []
    for i in range(num_instances):
        p = mp.Process(target=run_spider_instance, args=(i,))
        p.start()
        processes.append(p)
        logger.info(f"Launched scout instance {i} (PID: {p.pid})")

    # Wait for all processes to complete
    logger.info(f"All {num_instances} instances launched. Waiting for completion...")
    for i, p in enumerate(processes):
        p.join()
        logger.info(f"Scout instance {i} completed")

    logger.info("All scout instances completed")


if __name__ == "__main__":
    main()
