#!/usr/bin/env python
"""
Simple test to verify start_requests() is called
"""
import sys
import logging

logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

def main():
    print("=== Starting Simple Spider Test ===")

    # Get settings
    settings = get_project_settings()
    settings.set('LOG_LEVEL', 'WARNING')  # Reduce noise
    settings.set('CLOSESPIDER_TIMEOUT', 30)  # 30 second timeout
    settings.set('CLOSESPIDER_PAGECOUNT', 5)  # Stop after 5 pages

    # Create process
    process = CrawlerProcess(settings)

    # Crawl scout spider
    print("Crawling 'scout' spider...")
    process.crawl('scout')

    # Start (blocks until done)
    print("Starting crawler process...")
    process.start()

    print("=== Crawler Finished ===")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
