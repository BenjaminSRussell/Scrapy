#!/usr/bin/env python3
"""
Main entry point for running the Scrapy-based web scraping pipeline.
Uses ScoutSpider for high-speed URL discovery.
"""

import sys
import logging
from pathlib import Path
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.stage1.scout_spider import ScoutSpider

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)


def main():
    """Run the Scout Spider with Scrapy settings."""
    logger.info("=" * 80)
    logger.info("Starting Scrapy Pipeline - Scout Spider")
    logger.info("=" * 80)

    # Get Scrapy settings from settings.py
    settings = get_project_settings()

    # Create crawler process
    process = CrawlerProcess(settings)

    # Start the scout spider
    process.crawl(ScoutSpider)

    # Start crawling (blocking call)
    process.start()

    logger.info("=" * 80)
    logger.info("Pipeline completed")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
