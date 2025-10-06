#!/usr/bin/env python3
"""
Async pipeline orchestrator with intelligent routing.
"""

import sys
import argparse
import logging
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.common.delta_lake import get_delta_manager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_stage1_scout(seed_file: str = None):
    """Run Stage 1: Scout spider for standard pages."""
    from scrapy.crawler import CrawlerProcess
    from scrapy.utils.project import get_project_settings
    from src.stage1.scout_spider import ScoutSpider

    logger.info("=" * 80)
    logger.info("STAGE 1: SCOUT SPIDER (Ultra URL Discovery)")
    logger.info("=" * 80)

    settings = get_project_settings()
    process = CrawlerProcess(settings)

    process.crawl(ScoutSpider, seed_file=seed_file)
    process.start()

    logger.info("Scout complete")


async def run_stage1_jsbot():
    """Run Stage 1: JS rendering bot for complex pages."""
    from src.stage1.js_bot import run_js_bot

    logger.info("=" * 80)
    logger.info("STAGE 1: JS RENDERING BOT")
    logger.info("=" * 80)

    await run_js_bot()
    logger.info("JS bot complete")


def run_stage2():
    """Run Stage 2: Intelligent analysis with QC and triage."""
    from src.stage2.intelligent_analyzer import IntelligentAnalyzer

    logger.info("=" * 80)
    logger.info("STAGE 2: INTELLIGENT ANALYSIS")
    logger.info("=" * 80)

    delta = get_delta_manager()

    # Get URLs from stage 1
    stage1_data = delta.read('stage1_discovery')
    logger.info(f"Analyzing {len(stage1_data)} URLs")

    analyzer = IntelligentAnalyzer()
    batch = []
    low_quality_count = 0
    massive_doc_count = 0

    for i, record in enumerate(stage1_data):
        url = record.get('url')
        is_heavy = record.get('is_heavy', False)

        try:
            analysis = analyzer.analyze(url, is_heavy)
            analysis['url_hash'] = record.get('url_hash')

            # Quality control - discard low quality immediately
            if analysis.get('is_low_quality'):
                low_quality_count += 1
                continue

            # Triage - route massive docs to separate table
            if analysis.get('is_massive_doc'):
                delta.write('stage4_large_docs', [analysis], mode='append', async_write=False)
                massive_doc_count += 1
                continue

            batch.append(analysis)

            if len(batch) >= 50:
                delta.write('stage2_page_analysis', batch, mode='append', async_write=False)
                logger.info(f"Saved batch {i//50 + 1}")
                batch = []

        except Exception as e:
            logger.error(f"Failed to analyze {url}: {e}")

    # Save remaining
    if batch:
        delta.write('stage2_page_analysis', batch, mode='append', async_write=False)

    analyzer.close()
    
    logger.info(f"Stage 2 complete: {low_quality_count} low-quality discarded, {massive_doc_count} routed to large docs")


def run_stage3():
    """Run Stage 3: Analytics."""
    logger.info("=" * 80)
    logger.info("STAGE 3: ANALYTICS")
    logger.info("=" * 80)
    logger.info("Not yet implemented")


def run_stage4():
    """Run Stage 4: Summarization."""
    logger.info("=" * 80)
    logger.info("STAGE 4: SUMMARIZATION")
    logger.info("=" * 80)
    logger.info("Not yet implemented")


def show_stats():
    """Show Delta Lake statistics."""
    delta = get_delta_manager()

    logger.info("=" * 80)
    logger.info("DELTA LAKE STATISTICS")
    logger.info("=" * 80)

    for table_name in delta.tables.keys():
        count = delta.count(table_name)
        logger.info(f"{table_name}: {count} records")


def main():
    parser = argparse.ArgumentParser(description='Async Web Scraping Pipeline')
    parser.add_argument('stage', nargs='?', 
                        choices=['1', '1scout', '1js', '2', '3', '4', 'all', 'stats'], 
                        default='stats',
                        help='Which stage to run')
    parser.add_argument('--seed', type=str, help='Seed file for stage 1')

    args = parser.parse_args()

    try:
        if args.stage == '1' or args.stage == '1scout':
            run_stage1_scout(args.seed)
        elif args.stage == '1js':
            asyncio.run(run_stage1_jsbot())
        elif args.stage == '2':
            run_stage2()
        elif args.stage == '3':
            run_stage3()
        elif args.stage == '4':
            run_stage4()
        elif args.stage == 'all':
            run_stage1_scout(args.seed)
            asyncio.run(run_stage1_jsbot())
            run_stage2()
            run_stage3()
            run_stage4()
        elif args.stage == 'stats':
            show_stats()
        else:
            parser.print_help()

    except KeyboardInterrupt:
        logger.warning("\nInterrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
