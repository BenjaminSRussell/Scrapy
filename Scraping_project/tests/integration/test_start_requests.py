#!/usr/bin/env python

import logging
import sys

from scrapy import Spider
from scrapy.crawler import CrawlerProcess
from scrapy.http import Request
from scrapy.utils.project import get_project_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

class TestSpider(Spider):

    name = "test_start_requests"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        print("\n" + "=" * 80)
        print("TEST SPIDER INITIALIZED")
        print("=" * 80)
        self.start_urls = [
            "https://uconn.edu",
            "https://today.uconn.edu",
            "https://admissions.uconn.edu",
        ]
        print(f"Set start_urls: {len(self.start_urls)} URLs")
        for i, url in enumerate(self.start_urls, 1):
            print(f"  {i}. {url}")
        print("=" * 80 + "\n")

    def start_requests(self):
        print("\n" + "🚀" * 40)
        print("🚀 start_requests() CALLED!")
        print("🚀" * 40)
        print(f"Generating {len(self.start_urls)} requests...")
        print()

        for i, url in enumerate(self.start_urls, 1):
            print(f"  [{i}/{len(self.start_urls)}] Yielding request for: {url}")
            yield Request(
                url,
                callback=self.parse,
                dont_filter=True,
                priority=0,
            )

        print()
        print("✅" * 40)
        print(f"✅ start_requests() COMPLETED - Generated {len(self.start_urls)} requests")
        print("✅" * 40 + "\n")

    def parse(self, response):
        print("\n" + "📄" * 40)
        print(f"📄 parse() CALLED for: {response.url}")
        print(f"📄 Status: {response.status}")
        print(f"📄 Content-Length: {len(response.body)} bytes")
        print("📄" * 40 + "\n")
        return {}

def main():
    print("\n" + "=" * 80)
    print("TEST 2: Verify start_requests() is Called")
    print("=" * 80)
    print()
    print("Purpose: Check if start_requests() method is executed")
    print("Expected: See 🚀 messages when start_requests() is called")
    print("Expected: See 📄 messages when parse() receives responses")
    print()
    print("-" * 80 + "\n")

    settings = get_project_settings()

    settings.set("LOG_LEVEL", "WARNING")
    settings.set("CLOSESPIDER_PAGECOUNT", 3)
    settings.set("CLOSESPIDER_TIMEOUT", 30)

    process = CrawlerProcess(settings)

    print("Starting test spider...")
    process.crawl(TestSpider)

    print("Calling process.start()...")
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
    print("TEST 2 COMPLETED")
    print("=" * 80)
    print()
    print("ANALYSIS:")
    print("- If you see 🚀 messages: start_requests() IS being called")
    print("- If you see 📄 messages: Requests are being processed")
    print("- If you see NEITHER: There's a fundamental issue with Scrapy setup")
    print("=" * 80 + "\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)
