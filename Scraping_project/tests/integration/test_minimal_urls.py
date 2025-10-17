#!/usr/bin/env python
"""
Test 3: Minimal URLs Test (Bypass Delta Lake)
Purpose: Test with hardcoded URLs to bypass Delta Lake loading
"""

import logging
import sys

from scrapy import Spider
from scrapy.crawler import CrawlerProcess
from scrapy.http import Request
from scrapy.utils.project import get_project_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


class MinimalTestSpider(Spider):
    """Minimal spider with hardcoded URLs"""

    name = "test_minimal"

    # Hardcode 5 simple URLs (no Delta Lake)
    start_urls = [
        "https://uconn.edu",
        "https://today.uconn.edu",
        "https://admissions.uconn.edu",
        "https://lib.uconn.edu",
        "https://registrar.uconn.edu",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        print(f"\n✅ MinimalTestSpider initialized with {len(self.start_urls)} URLs")

    def start_requests(self):
        """Generate requests with logging"""
        print(f"\n🚀 start_requests() called - Generating {len(self.start_urls)} requests")

        for i, url in enumerate(self.start_urls, 1):
            print(f"  Request {i}: {url}")
            yield Request(
                url,
                callback=self.parse,
                errback=self.handle_error,
                dont_filter=True,
            )

        print(f"✅ Yielded {len(self.start_urls)} requests\n")

    def parse(self, response):
        """Parse callback"""
        print(f"📄 Parsed: {response.url} (Status: {response.status}, Size: {len(response.body)} bytes)")
        # Extract some links for visibility
        links = response.css("a::attr(href)").getall()[:5]
        if links:
            print(f"   Found {len(links)} sample links: {links}")
        return {"url": response.url, "status": response.status, "link_count": len(links)}

    def handle_error(self, failure):
        """Error handler"""
        print(f"❌ Error: {failure.request.url} - {failure.value}")


def main():
    print("\n" + "=" * 80)
    print("TEST 3: Minimal URLs Test (No Delta Lake)")
    print("=" * 80)
    print()
    print("Purpose: Test with 5 hardcoded URLs to verify basic crawling works")
    print("Expected: All 5 URLs should be crawled successfully")
    print()
    print("-" * 80 + "\n")

    # Get settings
    settings = get_project_settings()

    # Override settings for testing
    settings.set("LOG_LEVEL", "WARNING")
    settings.set("CLOSESPIDER_TIMEOUT", 60)  # 60 second timeout
    settings.set("CONCURRENT_REQUESTS", 2)  # Low concurrency for visibility
    settings.set("DUPEFILTER_CLASS", "scrapy.dupefilters.BaseDupeFilter")  # No filtering

    # Create process
    process = CrawlerProcess(settings)

    # Crawl test spider
    print("Starting minimal test spider...")
    process.crawl(MinimalTestSpider)

    # Start
    print("Starting crawl...\n")
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
    print("TEST 3 COMPLETED")
    print("=" * 80)
    print()
    print("ANALYSIS:")
    print("- If all 5 URLs were crawled: Basic Scrapy setup is working")
    print("- If 0 URLs were crawled: There's a fundamental Scrapy configuration issue")
    print("- If some URLs were crawled: Network or filtering issue")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)
