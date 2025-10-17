#!/usr/bin/env python
"""
Test 1: Single Scout Spider Instance
Purpose: Verify that a single scout spider can start and crawl without multiprocessing
"""

import logging
import sys

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main():
    print("=" * 80)
    print("TEST 1: Single Scout Spider Instance (No Multiprocessing)")
    print("=" * 80)
    print()
    print("Purpose: Verify that scout spider can start and crawl")
    print("Expected: Spider opens, start_requests() called, URLs crawled")
    print()
    print("-" * 80)

    # Get settings
    settings = get_project_settings()

    # Override settings for testing
    settings.set("LOG_LEVEL", "INFO")
    settings.set("CLOSESPIDER_PAGECOUNT", 10)  # Stop after 10 pages
    settings.set("CLOSESPIDER_TIMEOUT", 60)  # 60 second timeout

    # Create process
    process = CrawlerProcess(settings)

    # Crawl scout spider
    print("Starting scout spider...")
    process.crawl("scout")

    # Start (blocks until done)
    print("Calling process.start()...")
    print("-" * 80)
    print()

    try:
        process.start()
    except Exception as e:
        print()
        print("=" * 80)
        print(f"ERROR: {e}")
        import traceback

        traceback.print_exc()
        print("=" * 80)
        sys.exit(1)

    print()
    print("=" * 80)
    print("TEST 1 COMPLETED")
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)
