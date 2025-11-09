#!/usr/bin/env python

import logging
import sys

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

def main():
    print("=== Starting Simple Spider Test ===")

    settings = get_project_settings()
    settings.set("LOG_LEVEL", "WARNING")
    settings.set("CLOSESPIDER_TIMEOUT", 30)
    settings.set("CLOSESPIDER_PAGECOUNT", 5)

    process = CrawlerProcess(settings)

    print("Crawling 'scout' spider...")
    process.crawl("scout")

    print("Starting crawler process...")
    process.start()

    print("=== Crawler Finished ===")

if __name__ == "__main__":
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
