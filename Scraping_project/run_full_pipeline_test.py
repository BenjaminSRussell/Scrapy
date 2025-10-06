#!/usr/bin/env python3
"""Full pipeline test - runs limited crawl and processes through all stages."""

import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.stage2.stage2_worker import Stage2Worker
from src.stage3.stage3_worker import Stage3Worker
from src.common.delta_lake import get_delta_manager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)


async def run_limited_scrapy(max_pages: int = 5):
    """Run Scrapy with limited pages."""
    logger.info(f"Starting Scrapy crawler (limited to {max_pages} pages)")

    # Run scrapy with closespider_pagecount setting and test seeds
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'scrapy', 'crawl', 'scout',
        '-a', 'seed_file=test_seeds.txt',  # Use test seeds
        '-s', f'CLOSESPIDER_PAGECOUNT={max_pages}',
        '-s', 'DEPTH_LIMIT=0',  # No depth - just test seeds
        cwd=str(Path(__file__).parent),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    # Stream output
    async def log_stream(stream, prefix):
        async for line in stream:
            msg = line.decode().strip()
            if msg and not msg.startswith('DEBUG'):  # Skip debug messages
                logger.info(f"[{prefix}] {msg}")

    await asyncio.gather(
        log_stream(process.stdout, "SCRAPY"),
        log_stream(process.stderr, "SCRAPY-ERR"),
        return_exceptions=True
    )

    await process.wait()
    logger.info(f"Scrapy finished with code {process.returncode}")


async def run_stage2_once():
    """Run Stage 2 worker once."""
    logger.info("Starting Stage 2 worker")

    worker = Stage2Worker(max_concurrent=10, batch_size=100)

    try:
        await worker.run()
        logger.info("Stage 2 completed")
    except Exception as e:
        logger.error(f"Stage 2 failed: {e}", exc_info=True)


async def run_stage3_once():
    """Run Stage 3 worker once."""
    logger.info("Starting Stage 3 worker")

    worker = Stage3Worker(max_concurrent=5, batch_size=50)

    try:
        await worker.run()
        logger.info("Stage 3 completed")
    except Exception as e:
        logger.error(f"Stage 3 failed: {e}", exc_info=True)


async def check_results():
    """Check results in Delta Lake tables."""
    logger.info("\n" + "=" * 80)
    logger.info("CHECKING RESULTS")
    logger.info("=" * 80)

    delta = get_delta_manager()

    # Check stage1_discovery
    try:
        stage1_data = delta.read('stage1_discovery')
        logger.info(f"✅ Stage 1 (Discovery): {len(stage1_data)} URLs discovered")
        if stage1_data:
            logger.info(f"   Sample URL: {stage1_data[0].get('url', 'N/A')}")
    except Exception as e:
        logger.warning(f"❌ Stage 1 table error: {e}")

    # Check stage2_page_analysis
    try:
        stage2_data = delta.read('stage2_page_analysis')
        logger.info(f"✅ Stage 2 (Analysis): {len(stage2_data)} pages analyzed")

        if stage2_data:
            quality_docs = [d for d in stage2_data if not d.get('is_low_quality', True)]
            massive_docs = [d for d in stage2_data if d.get('is_massive_doc', False)]
            logger.info(f"   Quality documents: {len(quality_docs)}")
            logger.info(f"   Massive documents: {len(massive_docs)}")

            sample = stage2_data[0]
            logger.info(f"   Sample: {sample.get('url', 'N/A')[:80]}")
            logger.info(f"   - Title: {sample.get('title', 'N/A')[:60]}")
            logger.info(f"   - Word count: {sample.get('word_count', 0)}")
            logger.info(f"   - Quality score: {sample.get('quality_score', 0)}")
    except Exception as e:
        logger.warning(f"❌ Stage 2 table error: {e}")

    # Check stage3_summaries
    try:
        stage3_data = delta.read('stage3_summaries')
        logger.info(f"✅ Stage 3 (Summaries): {len(stage3_data)} summaries created")

        if stage3_data:
            sample = stage3_data[0]
            logger.info(f"   Sample summary for: {sample.get('url', 'N/A')[:80]}")
            logger.info(f"   - Summary: {sample.get('summary', 'N/A')[:100]}...")
    except Exception as e:
        logger.warning(f"❌ Stage 3 table error: {e}")

    # Check stage4_large_docs
    try:
        stage4_data = delta.read('stage4_large_docs')
        logger.info(f"✅ Stage 4 (Large Docs Queue): {len(stage4_data)} documents queued")

        if stage4_data:
            pending = [d for d in stage4_data if d.get('status') == 'pending']
            logger.info(f"   Pending processing: {len(pending)}")
    except Exception as e:
        logger.info(f"ℹ️  Stage 4 table: {e}")

    logger.info("=" * 80 + "\n")


async def main():
    """Run complete pipeline test."""
    logger.info("=" * 80)
    logger.info("FULL PIPELINE TEST - LIMITED RUN")
    logger.info("=" * 80)

    # Stage 1: Discovery (limited)
    await run_limited_scrapy(max_pages=5)

    # Small delay to ensure data is written
    await asyncio.sleep(2)

    # Stage 2: Analysis
    await run_stage2_once()

    # Small delay
    await asyncio.sleep(1)

    # Stage 3: Summarization
    await run_stage3_once()

    # Check results
    await check_results()

    logger.info("=" * 80)
    logger.info("PIPELINE TEST COMPLETED")
    logger.info("=" * 80)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        sys.exit(1)
