#!/usr/bin/env python
"""
Simple Scrapy runner using CrawlerProcess
"""

import logging
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def main():
    """Run the scout spider using CrawlerProcess."""
    settings = get_project_settings()

    # Create process
    process = CrawlerProcess(settings)

    # Crawl the scout spider
    process.crawl('scout')

    # Start the process (this blocks until finished)
    logger.info("Starting Scrapy crawler...")
    process.start()


if __name__ == '__main__':
    main()
