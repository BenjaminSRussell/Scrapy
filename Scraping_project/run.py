#!/usr/bin/env python
"""
Scrapy Application Runner with CrawlerRunner
==============================================
This script serves as the Docker container entrypoint for running Scrapy spiders.
It uses CrawlerRunner for programmatic control over spider execution.

Features:
- Programmatic spider configuration and execution
- Proper Twisted reactor lifecycle management
- Graceful shutdown handling (SIGTERM, SIGINT)
- Multiple spider orchestration
- Deferred-based async coordination
"""

import logging
import signal
import sys

# Install the asyncio reactor BEFORE any other Twisted imports
# This prevents reactor conflicts
import asyncio
from twisted.internet import asyncioreactor
asyncioreactor.install(asyncio.new_event_loop())

from scrapy.crawler import CrawlerRunner
from scrapy.utils.log import configure_logging
from scrapy.utils.project import get_project_settings
from twisted.internet import defer, reactor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class ScrapyRunner:
    """Orchestrates multiple Scrapy spiders using CrawlerRunner."""

    def __init__(self, spider_names: list[str] = None):
        """Initialize the Scrapy runner.

        Args:
            spider_names: List of spider names to run. If None, runs all spiders.
        """
        # Load Scrapy project settings
        self.settings = get_project_settings()

        # Configure Scrapy logging (uses settings from settings.py)
        configure_logging(self.settings)

        # Create CrawlerRunner instance
        self.runner = CrawlerRunner(self.settings)

        # List of spiders to run
        self.spider_names = spider_names or ['scout']  # Default to scout spider

        # Shutdown flag
        self.shutdown_requested = False

        logger.info(f"Scrapy runner initialized with settings module: {self.settings.get('BOT_NAME')}")
        logger.info(f"Spiders to run: {', '.join(self.spider_names)}")

    def setup_signal_handlers(self):
        """Set up signal handlers for graceful shutdown."""

        def signal_handler(signum, frame):
            """Handle shutdown signals."""
            sig_name = signal.Signals(signum).name
            logger.info(f"Received {sig_name} signal, initiating graceful shutdown...")
            self.shutdown_requested = True

            # Stop the reactor gracefully
            if reactor.running:
                reactor.callFromThread(reactor.stop)

        # Register signal handlers
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        logger.info("Signal handlers registered for SIGTERM and SIGINT")

    @defer.inlineCallbacks
    def run_spiders(self):
        """Run all configured spiders sequentially or in parallel.

        This method uses Twisted's inlineCallbacks decorator to handle
        the asynchronous nature of Scrapy crawls.

        Yields:
            Deferred objects from spider crawls
        """
        logger.info("Starting spider crawls...")

        try:
            # Option 1: Run spiders sequentially (one after another)
            # Uncomment this block to run spiders one at a time
            # for spider_name in self.spider_names:
            #     if self.shutdown_requested:
            #         logger.warning(f"Shutdown requested, skipping spider: {spider_name}")
            #         break
            #     logger.info(f"Starting spider: {spider_name}")
            #     yield self.runner.crawl(spider_name)
            #     logger.info(f"Completed spider: {spider_name}")

            # Option 2: Run spiders in parallel (all at once)
            # This is more efficient but uses more resources
            deferreds = []
            for spider_name in self.spider_names:
                if self.shutdown_requested:
                    logger.warning(f"Shutdown requested, skipping spider: {spider_name}")
                    break
                logger.info(f"Scheduling spider: {spider_name}")
                deferred = self.runner.crawl(spider_name)
                deferreds.append(deferred)

            # Wait for all spiders to complete
            if deferreds:
                yield defer.DeferredList(deferreds)

            logger.info("All spiders completed successfully")

        except Exception as e:
            logger.error(f"Error during spider execution: {e}", exc_info=True)
            raise

        finally:
            # Stop the reactor after all spiders complete
            if reactor.running:
                reactor.stop()

    def _start_spiders_when_running(self):
        """Helper to start spiders after reactor is running."""
        deferred = self.run_spiders()
        deferred.addErrback(lambda failure: logger.error(f"Spider execution failed: {failure}"))

    def start(self):
        """Start the Scrapy runner and Twisted reactor."""
        logger.info("Starting Scrapy application...")

        # Set up signal handlers for graceful shutdown
        self.setup_signal_handlers()

        # Schedule the spider crawls to run when reactor starts
        reactor.callWhenRunning(self._start_spiders_when_running)

        # Start the Twisted reactor
        # The reactor is Scrapy's event loop that manages all async operations
        logger.info("Starting Twisted reactor...")
        reactor.run()

        logger.info("Scrapy application stopped")


def main():
    """Main entry point for the Scrapy runner."""
    # Parse command-line arguments (optional)
    # You can extend this to accept spider names from CLI
    import argparse

    parser = argparse.ArgumentParser(description='Run Scrapy spiders')
    parser.add_argument(
        '--spiders',
        nargs='+',
        default=None,
        help='List of spider names to run (default: scout)'
    )
    args = parser.parse_args()

    # Create and start the runner
    runner = ScrapyRunner(spider_names=args.spiders)

    try:
        runner.start()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
