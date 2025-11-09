#!/usr/bin/env python
"""Run complete pipeline: Stage 1 -> Stage 2 -> Stage 3"""
import asyncio
import sys
from pathlib import Path

# Add project to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.common.storage_manager import get_delta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_stage1():
    """Run Stage 1 (Scout spider) to populate stage2_queue"""
    from scrapy.crawler import CrawlerProcess
    from scrapy.utils.project import get_project_settings

    logger.info("=" * 60)
    logger.info("STAGE 1: URL DISCOVERY")
    logger.info("=" * 60)

    settings = get_project_settings()

    # Disable problematic middleware
    settings.set('SPIDER_MIDDLEWARES', {
        'src.pipelines.QueueItemPipeline': 100,  # Keep queue pipeline
    })
    settings.set('DOWNLOADER_MIDDLEWARES', {})
    settings.set('EXTENSIONS', {})

    # Limit for testing
    settings.set('CLOSESPIDER_ITEMCOUNT', 20)
    settings.set('LOG_LEVEL', 'INFO')
    settings.set('TWISTED_REACTOR', 'twisted.internet.selectreactor.SelectReactor')

    process = CrawlerProcess(settings)
    process.crawl('scout')
    process.start()

    logger.info("✅ Stage 1 complete")

async def run_stage2():
    """Run Stage 2 worker to analyze queued URLs"""
    from src.stage2.stage2_worker import Stage2Worker

    logger.info("=" * 60)
    logger.info("STAGE 2: PAGE ANALYSIS")
    logger.info("=" * 60)

    worker = Stage2Worker(max_concurrent=10, batch_size=20)
    await worker.run()

    logger.info("✅ Stage 2 complete")

def check_results():
    """Check results at each stage"""
    delta = get_delta()

    logger.info("=" * 60)
    logger.info("PIPELINE RESULTS")
    logger.info("=" * 60)

    try:
        seeds = delta.read_table('seed_urls')
        logger.info(f"📊 seed_urls: {len(seeds)} URLs")
    except Exception as e:
        logger.warning(f"seed_urls: {e}")

    try:
        queue = delta.read_table('stage2_queue')
        logger.info(f"📊 stage2_queue: {len(queue)} URLs queued for analysis")
    except Exception as e:
        logger.warning(f"stage2_queue: {e}")

    try:
        analysis = delta.read_table('stage2_page_analysis')
        logger.info(f"📊 stage2_page_analysis: {len(analysis)} pages analyzed")
    except Exception as e:
        logger.warning(f"stage2_page_analysis: {e}")

if __name__ == '__main__':
    import os
    os.chdir(project_root)

    logger.info("\n🚀 Starting Full Pipeline Test\n")

    # Stage 1: Discover URLs and queue them
    logger.info("Starting Stage 1...")
    run_stage1()

    logger.info("\n" + "=" * 60)
    logger.info("Checking Stage 1 output...")
    check_results()

    # Stage 2: Analyze queued URLs
    logger.info("\nStarting Stage 2...")
    asyncio.run(run_stage2())

    logger.info("\n" + "=" * 60)
    logger.info("Final Results:")
    check_results()

    logger.info("\n✅ Full pipeline test complete!")
