#!/usr/bin/env python3
"""
3-Stage Pipeline Runner

Stage 1: Discovery - Gather URLs with metadata
Stage 2: Analytics - Extract data (OCR, Whisper, YAKE, clean HTML)
Stage 3: Summarization - Heavy model summary + JSONL output
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.common.constants import LOGS_DIR, LOG_FORMAT
from src.common.delta_lake import get_delta_manager

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.FileHandler(LOGS_DIR / "pipeline.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def run_stage1(seed_file: str, max_depth: int):
    """Stage 1: URL Discovery with metadata."""
    from scrapy.crawler import CrawlerProcess
    from scrapy.utils.project import get_project_settings
    from src.stage1.discovery import DiscoverySpider

    logger.info("=" * 80)
    logger.info("STAGE 1: URL DISCOVERY + METADATA")
    logger.info("=" * 80)

    settings = get_project_settings()
    settings.update({
        'TELNETCONSOLE_ENABLED': False,
        'ROBOTSTXT_OBEY': False,
        'CONCURRENT_REQUESTS': 32,
        'DOWNLOAD_DELAY': 0.1,
        'LOG_LEVEL': 'INFO',
    })

    process = CrawlerProcess(settings)
    process.crawl(DiscoverySpider, seed_file=seed_file, max_depth=max_depth)

    try:
        process.start()
        logger.info("✅ Stage 1 complete")
    except Exception as e:
        logger.error(f"Stage 1 failed: {e}", exc_info=True)
        raise


def run_stage2():
    """Stage 2: Analytics - OCR, Whisper, YAKE, data extraction."""
    from src.stage2.analytics import analyze_url

    logger.info("=" * 80)
    logger.info("STAGE 2: ANALYTICS (OCR + Whisper + YAKE)")
    logger.info("=" * 80)

    delta = get_delta_manager()

    # Get URLs from stage 1
    stage1_data = delta.read('stage1_discovery')

    if not stage1_data:
        logger.warning("No URLs from stage 1")
        return

    logger.info(f"Analyzing {len(stage1_data)} URLs...")

    analytics_results = []

    for record in stage1_data:
        url = record.get('url')
        metadata = record.get('metadata', {})

        if not url:
            continue

        # Analyze URL
        analytics = analyze_url(url, metadata)
        analytics_results.append(analytics)

        # Save to Delta Lake asynchronously
        delta.write('stage2_analytics', [analytics], async_write=True)

        if len(analytics_results) % 10 == 0:
            logger.info(f"Analyzed {len(analytics_results)} URLs...")

    logger.info(f"✅ Stage 2 complete: {len(analytics_results)} URLs analyzed")


def run_stage3(output_file: str = None):
    """Stage 3: Heavy summarization + JSONL output."""
    from src.stage3.summarization import create_final_summary, save_to_jsonl

    logger.info("=" * 80)
    logger.info("STAGE 3: SUMMARIZATION + FINAL OUTPUT")
    logger.info("=" * 80)

    delta = get_delta_manager()

    # Get analytics from stage 2
    stage2_data = delta.read('stage2_analytics')

    if not stage2_data:
        logger.warning("No analytics from stage 2")
        return

    logger.info(f"Summarizing {len(stage2_data)} URLs...")

    summaries = []

    for analytics in stage2_data:
        # Create summary
        summary = create_final_summary(analytics)
        summaries.append(summary)

        # Save to Delta Lake asynchronously
        delta.write('stage3_summaries', [summary], async_write=True)

        if len(summaries) % 10 == 0:
            logger.info(f"Summarized {len(summaries)} URLs...")

    # Save to JSONL file
    output_path = Path(output_file) if output_file else None
    jsonl_file = save_to_jsonl(summaries, output_path)

    logger.info(f"✅ Stage 3 complete: {len(summaries)} summaries")
    logger.info(f"✅ Final output: {jsonl_file}")


def main():
    parser = argparse.ArgumentParser(description="3-Stage Scraping Pipeline")
    parser.add_argument(
        "--stage",
        choices=["1", "2", "3", "all"],
        default="all",
        help="Which stage to run"
    )
    parser.add_argument(
        "--seed",
        default="data/raw/uconn_urls.csv",
        help="Seed URLs file for stage 1"
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=3,
        help="Maximum crawl depth for stage 1"
    )
    parser.add_argument(
        "--output",
        default="data/final_summaries.jsonl",
        help="Output JSONL file for stage 3"
    )

    args = parser.parse_args()

    try:
        if args.stage in ["1", "all"]:
            run_stage1(args.seed, args.max_depth)

        if args.stage in ["2", "all"]:
            run_stage2()

        if args.stage in ["3", "all"]:
            run_stage3(args.output)

        # Checkpoint Delta Lake
        logger.info("=" * 80)
        logger.info("CHECKPOINTING DELTA LAKE")
        logger.info("=" * 80)

        delta = get_delta_manager()
        delta.checkpoint()

        # Show counts
        logger.info(f"Stage 1 records: {delta.count('stage1_discovery')}")
        logger.info(f"Stage 2 records: {delta.count('stage2_analytics')}")
        logger.info(f"Stage 3 records: {delta.count('stage3_summaries')}")

        logger.info("✅ PIPELINE COMPLETE!")

    except KeyboardInterrupt:
        logger.info("Pipeline interrupted")
        get_delta_manager().checkpoint()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        get_delta_manager().checkpoint()
        sys.exit(1)


if __name__ == "__main__":
    main()
