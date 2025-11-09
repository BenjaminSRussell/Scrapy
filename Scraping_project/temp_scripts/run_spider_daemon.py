#!/usr/bin/env python
"""Daemon wrapper for running Scrapy spiders without event loop conflicts"""
import sys
import os
from pathlib import Path

# Add project to path
project_root = Path(__file__).parent.parent
os.chdir(project_root)
sys.path.insert(0, str(project_root))

# Set environment
os.environ['SCRAPY_SETTINGS_MODULE'] = 'src.settings'

# Import after setting up environment
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

def run_spider(spider_name='scout', item_limit=100):
    """Run spider in a properly configured process"""
    print(f"🕷️ Starting {spider_name} spider (limit: {item_limit} items)")

    settings = get_project_settings()
    settings.set('CLOSESPIDER_ITEMCOUNT', item_limit)
    settings.set('LOG_FILE', 'data/logs/scout_spider.log')
    settings.set('LOG_LEVEL', 'INFO')
    # Use select reactor to avoid asyncio conflicts
    settings.set('TWISTED_REACTOR', 'twisted.internet.selectreactor.SelectReactor')

    process = CrawlerProcess(settings)
    process.crawl(spider_name)

    print(f"✅ Starting crawler...")
    process.start()  # Blocks until finished

    print(f"✅ Spider {spider_name} completed")

if __name__ == '__main__':
    spider_name = sys.argv[1] if len(sys.argv) > 1 else 'scout'
    item_limit = int(sys.argv[2]) if len(sys.argv) > 2 else 100

    run_spider(spider_name, item_limit)
