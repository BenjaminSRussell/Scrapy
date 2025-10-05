#!/usr/bin/env python3
"""
Start script for the scraping pipeline.
Usage: python start.py [stage] [options]
"""

import sys
import argparse
import logging
from pathlib import Path

# Setup path
sys.path.insert(0, str(Path(__file__).parent))

from src.common.delta_lake import get_delta_manager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_stage1(seed_file: str = None):
    """Run Stage 1: Ultra-aggressive URL discovery."""
    from scrapy.crawler import CrawlerProcess
    from scrapy.utils.project import get_project_settings
    from src.stage1.discovery import DiscoverySpider

    logger.info("=" * 80)
    logger.info("STAGE 1: ULTRA-AGGRESSIVE URL DISCOVERY")
    logger.info("=" * 80)

    settings = get_project_settings()
    process = CrawlerProcess(settings)

    process.crawl(DiscoverySpider, seed_file=seed_file)
    process.start()

    logger.info("Stage 1 complete")


def run_stage2():
    """Run Stage 2: Page analysis with word count, errors, PDF+OCR, YAKE."""
    from src.stage2.page_analysis import PageAnalyzer

    logger.info("=" * 80)
    logger.info("STAGE 2: PAGE ANALYSIS")
    logger.info("=" * 80)

    delta = get_delta_manager()

    # Get URLs from stage 1
    stage1_data = delta.read('stage1_discovery')
    logger.info(f"Analyzing {len(stage1_data)} URLs from Stage 1")

    analyzer = PageAnalyzer()
    batch = []

    for i, record in enumerate(stage1_data):
        url = record.get('url')
        is_heavy = record.get('is_heavy', False)

        try:
            analysis = analyzer.analyze(url, is_heavy)
            analysis['url_hash'] = record.get('url_hash')
            analysis['from_stage1'] = True
            batch.append(analysis)

            if len(batch) >= 50:
                delta.write('stage2_page_analysis', batch, mode='append', async_write=False)
                logger.info(f"Saved batch {i//50 + 1} ({len(batch)} records)")
                batch = []

        except Exception as e:
            logger.error(f"Failed to analyze {url}: {e}")

    # Save remaining
    if batch:
        delta.write('stage2_page_analysis', batch, mode='append', async_write=False)
        logger.info(f"Saved final batch ({len(batch)} records)")

    analyzer.close()
    logger.info("Stage 2 complete")


def run_stage3():
    """Run Stage 3: Analytics (old stage 2)."""
    logger.info("=" * 80)
    logger.info("STAGE 3: ANALYTICS")
    logger.info("=" * 80)
    logger.info("Not yet implemented")


def run_stage4():
    """Run Stage 4: Summarization (old stage 3)."""
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
    parser = argparse.ArgumentParser(description='Web Scraping Pipeline')
    parser.add_argument('stage', nargs='?', choices=['1', '2', '3', '4', 'all', 'stats'], default='stats',
                        help='Which stage to run (default: stats)')
    parser.add_argument('--seed', type=str, help='Seed file for stage 1')

    args = parser.parse_args()

    try:
        if args.stage == '1':
            run_stage1(args.seed)
        elif args.stage == '2':
            run_stage2()
        elif args.stage == '3':
            run_stage3()
        elif args.stage == '4':
            run_stage4()
        elif args.stage == 'all':
            run_stage1(args.seed)
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
