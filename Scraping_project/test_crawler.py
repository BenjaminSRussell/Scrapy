#!/usr/bin/env python3
"""Simple test to verify crawler works with asyncioreactor"""

import asyncio
import sys
from pathlib import Path

# Fix Windows event loop
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Add src to path
src_dir = Path(__file__).parent / "src"
sys.path.insert(0, str(src_dir))

# Install asyncioreactor BEFORE importing twisted
from scrapy.crawler import CrawlerRunner
from twisted.internet import asyncioreactor
asyncioreactor.install()
from twisted.internet import defer, reactor

from src.stage1.discovery_spider import DiscoverySpider
from scrapy.utils.project import get_project_settings


async def main():
    print("Starting test crawler...")

    settings = get_project_settings()
    settings.update({
        'LOG_LEVEL': 'INFO',
        'TELNETCONSOLE_ENABLED': False,
        'STAGE1_OUTPUT_FILE': 'data/processed/stage01/test_crawler_output.jsonl',
        'SEED_FILE': 'data/raw/uconn_urls.csv',
        'USE_PERSISTENT_DEDUP': False,  # Disable for simple test
    })

    runner = CrawlerRunner(settings)

    deferred = runner.crawl(
        DiscoverySpider,
        max_depth=1  # Just one level for testing
    )

    print("Waiting for crawl to complete...")
    loop = asyncio.get_running_loop()
    await defer.Deferred.asFuture(deferred, loop)

    print("Crawl completed!")


if __name__ == '__main__':
    asyncio.run(main())
