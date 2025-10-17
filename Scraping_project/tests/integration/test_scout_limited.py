#!/usr/bin/env python
"""
Test 4: Scout Spider with Limited URLs from Delta Lake
Purpose: Test the actual scout spider but limit to first 10 URLs from Delta Lake
"""

import logging
import sys

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

# Monkey-patch the scout spider to limit URLs
from src.stage1 import scout_spider

# Store original _load_seed_urls
original_load_seed_urls = scout_spider.ScoutSpider._load_seed_urls if hasattr(scout_spider, "ScoutSpider") else None


def limited_load_seed_urls(self):
    """Load only first 10 seed URLs for testing"""
    print("\n🔧 MONKEY PATCH: Loading only first 10 seed URLs...")
    try:
        from src.common.storage_manager import get_delta_manager

        delta = get_delta_manager()
        seed_records = delta.read("seed_urls")
        all_urls = [record["url"] for record in seed_records]
        limited_urls = all_urls[:10]  # Only first 10
        print(f"✅ Loaded {len(limited_urls)}/{len(all_urls)} seed URLs (limited for testing)\n")
        for i, url in enumerate(limited_urls, 1):
            print(f"  {i}. {url}")
        print()
        return limited_urls
    except Exception as e:
        print(f"❌ Error loading seeds: {e}")
        return []


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main():
    print("\n" + "=" * 80)
    print("TEST 4: Scout Spider with Limited URLs (10 URLs from Delta Lake)")
    print("=" * 80)
    print()
    print("Purpose: Test actual scout spider with reduced URL set")
    print("Expected: Scout spider crawls first 10 URLs from seed_urls table")
    print()
    print("-" * 80 + "\n")

    # Apply monkey patch
    if original_load_seed_urls:
        from src.stage1.base_spider import BaseSpider

        BaseSpider._load_seed_urls = limited_load_seed_urls
        print("✅ Monkey patch applied to BaseSpider._load_seed_urls")
    else:
        print("⚠️  Could not apply monkey patch - ScoutSpider not found")

    # Get settings
    settings = get_project_settings()

    # Override settings for testing
    settings.set("LOG_LEVEL", "INFO")
    settings.set("CLOSESPIDER_PAGECOUNT", 10)  # Stop after 10 pages
    settings.set("CLOSESPIDER_TIMEOUT", 60)  # 60 second timeout
    settings.set("CONCURRENT_REQUESTS", 4)  # Low concurrency

    # Create process
    process = CrawlerProcess(settings)

    # Crawl scout spider
    print("\nStarting scout spider (limited to 10 URLs)...")
    process.crawl("scout")

    # Start
    print("Starting crawl...")
    print("-" * 80 + "\n")

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

    print("\n" + "=" * 80)
    print("TEST 4 COMPLETED")
    print("=" * 80)
    print()
    print("ANALYSIS:")
    print("- If URLs were crawled: Scout spider works with limited URLs")
    print("- If no URLs crawled: Issue is with scout spider logic, not URL count")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)
