#!/usr/bin/env python3
"""
Monitor pipeline queues and trigger depth spider when idle.

This script monitors the Redis priority queue and Stage 2 queue depths.
When both fall below a threshold, it automatically launches the depth spider
to perform intensive URL discovery and re-scraping.

Usage:
    python monitor_and_trigger_depth.py [--check-interval SECONDS]
"""

import argparse
import logging
import subprocess
import time
from datetime import datetime

import redis
import yaml

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class DepthSpiderMonitor:
    """Monitor pipeline state and trigger depth spider when queues are idle."""

    def __init__(self, config_path: str = "config.yml", check_interval: int = 60):
        """
        Initialize monitor.

        Args:
            config_path: Path to config.yml
            check_interval: Seconds between queue depth checks
        """
        self.config_path = config_path
        self.check_interval = check_interval

        # Load configuration
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        # Get depth spider settings
        stage1_config = self.config.get("stage1", {})
        depth_config = stage1_config.get("depth_spider", {})

        self.enabled = depth_config.get("enabled", True)
        self.trigger_threshold = depth_config.get("trigger_when_queue_below", 10)

        # Redis connection
        redis_config = self.config.get("redis", {})
        self.redis_client = redis.Redis(
            host=redis_config.get("host", "localhost"),
            port=redis_config.get("port", 6379),
            db=redis_config.get("db", 0),
            password=redis_config.get("password"),
            decode_responses=True,
        )

        # State tracking
        self.depth_spider_running = False
        self.last_depth_spider_start = None
        self.cooldown_hours = 4  # Don't restart depth spider within 4 hours

        logger.info(f"DepthSpiderMonitor initialized (trigger threshold: {self.trigger_threshold})")
        logger.info(f"Depth spider enabled: {self.enabled}")

    def get_queue_depths(self) -> dict[str, int]:
        """
        Get current queue depths from Redis.

        Returns:
            Dictionary with queue names and their depths
        """
        try:
            queues = {
                "stage1_priority": self.redis_client.llen("stage1:priority_queue") or 0,
                "stage2_priority": self.redis_client.llen("stage2:priority_queue") or 0,
                "js_spider_queue": self.redis_client.llen("js_spider:priority_queue") or 0,
                "scout_pending": self.redis_client.llen("scout:pending_urls") or 0,
            }
            return queues
        except Exception as e:
            logger.error(f"Failed to get queue depths: {e}")
            return {}

    def should_trigger_depth_spider(self, queue_depths: dict[str, int]) -> bool:
        """
        Determine if depth spider should be triggered.

        Args:
            queue_depths: Current queue depths

        Returns:
            True if depth spider should start
        """
        if not self.enabled:
            return False

        # Check if depth spider is already running
        if self.depth_spider_running:
            logger.debug("Depth spider already running")
            return False

        # Check cooldown period
        if self.last_depth_spider_start:
            hours_since_last_run = (datetime.now() - self.last_depth_spider_start).total_seconds() / 3600
            if hours_since_last_run < self.cooldown_hours:
                logger.debug(f"Depth spider in cooldown (last run: {hours_since_last_run:.1f}h ago)")
                return False

        # Check queue depths
        total_pending = sum(queue_depths.values())

        if total_pending < self.trigger_threshold:
            logger.info(f"Queue depths below threshold ({total_pending} < {self.trigger_threshold})")
            return True

        return False

    def start_depth_spider(self):
        """Start the depth spider in a subprocess."""
        try:
            logger.info("Starting depth spider...")

            # Start depth spider using scrapy command
            cmd = ["scrapy", "crawl", "depth"]

            # Run in background
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=".",
            )

            self.depth_spider_running = True
            self.last_depth_spider_start = datetime.now()

            logger.info(f"Depth spider started (PID: {process.pid})")

            # Monitor process (non-blocking)
            return process

        except Exception as e:
            logger.error(f"Failed to start depth spider: {e}", exc_info=True)
            self.depth_spider_running = False
            return None

    def check_depth_spider_status(self, process: subprocess.Popen | None) -> bool:
        """
        Check if depth spider process is still running.

        Args:
            process: Subprocess object

        Returns:
            True if still running, False if finished
        """
        if not process:
            self.depth_spider_running = False
            return False

        # Check if process has finished
        if process.poll() is not None:
            # Process finished
            stdout, stderr = process.communicate()
            logger.info(f"Depth spider finished with return code: {process.returncode}")

            if process.returncode != 0:
                logger.error(f"Depth spider stderr: {stderr.decode('utf-8', errors='ignore')}")

            self.depth_spider_running = False
            return False

        return True

    def run(self):
        """Main monitoring loop."""
        logger.info("Starting depth spider monitoring loop...")
        logger.info(f"Check interval: {self.check_interval}s")

        depth_spider_process = None

        try:
            while True:
                # Get current queue depths
                queue_depths = self.get_queue_depths()

                if queue_depths:
                    total_pending = sum(queue_depths.values())
                    logger.info(f"Queue depths: {queue_depths} (total: {total_pending})")

                # Check if depth spider process has finished
                if depth_spider_process:
                    still_running = self.check_depth_spider_status(depth_spider_process)
                    if not still_running:
                        depth_spider_process = None

                # Trigger depth spider if conditions are met
                if not depth_spider_process and self.should_trigger_depth_spider(queue_depths):
                    depth_spider_process = self.start_depth_spider()

                # Sleep before next check
                time.sleep(self.check_interval)

        except KeyboardInterrupt:
            logger.info("Monitoring stopped by user")
            if depth_spider_process:
                logger.info("Terminating depth spider process...")
                depth_spider_process.terminate()
                depth_spider_process.wait(timeout=10)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Monitor pipeline queues and trigger depth spider when idle")
    parser.add_argument(
        "--check-interval",
        type=int,
        default=60,
        help="Seconds between queue depth checks (default: 60)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.yml",
        help="Path to config.yml (default: config.yml)",
    )

    args = parser.parse_args()

    # Create and run monitor
    monitor = DepthSpiderMonitor(
        config_path=args.config,
        check_interval=args.check_interval,
    )
    monitor.run()


if __name__ == "__main__":
    main()
