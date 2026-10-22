#!/usr/bin/env python3
"""
Run scout spider instances in parallel, restarting crashed children.

Each child is a full scout crawl, so ``SCOUT_INSTANCES`` multiplies crawl
work inside one pod. Scale with ``replicaCount``/HPA rather than raising it.
The parent supervises its children: a child that exits non-zero is restarted
with capped exponential backoff, and the parent exits non-zero if any child
failed, so the pod's liveness probe sees the failure.
"""

import logging
import multiprocessing as mp
import os
import time
from multiprocessing.connection import wait

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

MAX_RESTARTS = int(os.getenv("SCOUT_MAX_RESTARTS", "5"))
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_CAP_SECONDS = 60.0
POLL_SECONDS = 1.0

try:
    from prometheus_client import Gauge, start_http_server

    SCOUT_INSTANCES_ALIVE = Gauge("scout_instances_alive", "Scout child processes currently alive")
    SUPERVISOR_METRICS_PORT = int(os.getenv("SCOUT_SUPERVISOR_METRICS_PORT", "9409"))
except Exception:  # prometheus_client missing or metric already registered
    SCOUT_INSTANCES_ALIVE = None
    SUPERVISOR_METRICS_PORT = None


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


def _spawn(instance_id: int, target=run_spider_instance) -> mp.Process:
    p = mp.Process(target=target, args=(instance_id,), name=f"scout-{instance_id}")
    p.start()
    logger.info(f"Launched scout instance {instance_id} (PID: {p.pid})")
    return p


def supervise(num_instances: int, max_restarts: int = MAX_RESTARTS, target=run_spider_instance) -> int:
    """Keep ``num_instances`` children running; return 0 only if none ever failed.

    A child that exits non-zero is restarted after an exponential backoff, up
    to ``max_restarts`` times. Once its budget is spent the slot stays down.
    """
    restarts = dict.fromkeys(range(num_instances), 0)
    running = {i: _spawn(i, target) for i in range(num_instances)}
    pending = {}  # instance_id -> monotonic time the restart is due
    any_failed = False

    while running or pending:
        now = time.monotonic()
        for i, due in list(pending.items()):
            if due <= now:
                del pending[i]
                running[i] = _spawn(i, target)

        if SCOUT_INSTANCES_ALIVE is not None:
            SCOUT_INSTANCES_ALIVE.set(len(running))

        timeout = min([POLL_SECONDS] + [max(0.0, due - now) for due in pending.values()])
        if running:
            wait([p.sentinel for p in running.values()], timeout=timeout)
        else:
            time.sleep(timeout)

        for i, p in list(running.items()):
            if p.exitcode is None:
                continue
            p.join()
            del running[i]
            if p.exitcode == 0:
                logger.info(f"Scout instance {i} completed")
                continue

            any_failed = True
            if restarts[i] >= max_restarts:
                logger.error(f"Scout instance {i} exited {p.exitcode}; restart budget ({max_restarts}) spent")
                continue
            restarts[i] += 1
            delay = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * 2 ** (restarts[i] - 1))
            logger.warning(
                f"Scout instance {i} exited {p.exitcode}; restart {restarts[i]}/{max_restarts} in {delay:.0f}s"
            )
            pending[i] = time.monotonic() + delay

    if SCOUT_INSTANCES_ALIVE is not None:
        SCOUT_INSTANCES_ALIVE.set(0)
    return 1 if any_failed else 0


def main():
    """Run multiple scout spider instances in parallel."""
    # One scout per pod by default; scale out with replicas/HPA instead.
    num_instances = int(os.getenv("SCOUT_INSTANCES", "1"))

    settings = get_project_settings()
    concurrent = settings.getint("CONCURRENT_REQUESTS")
    per_domain = settings.getint("CONCURRENT_REQUESTS_PER_DOMAIN")

    logger.info(f"Launching {num_instances} concurrent scout spider instances")
    logger.info("Each instance uses settings concurrency:")
    logger.info(f"  - CONCURRENT_REQUESTS: {concurrent}")
    logger.info(f"  - CONCURRENT_REQUESTS_PER_DOMAIN: {per_domain}")
    logger.info(f"  - Total theoretical max: {num_instances * concurrent} concurrent requests")

    if SCOUT_INSTANCES_ALIVE is not None and SUPERVISOR_METRICS_PORT:
        start_http_server(SUPERVISOR_METRICS_PORT)

    exit_code = supervise(num_instances)

    if exit_code:
        logger.error("One or more scout instances failed")
    else:
        logger.info("All scout instances completed")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
