#!/usr/bin/env python3
"""
Asynchronous Pipeline Orchestrator
Manages concurrent execution of:
- Stage 1: Scrapy URL Discovery (ScoutSpider + JSBot)
- Stage 2: Page Analysis & Quality Control
- Stage 3: Similarity Detection & Summarization
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.stage2.stage2_worker import Stage2Worker
from src.stage3.stage3_worker import Stage3Worker

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)


async def run_scrapy_crawler():
    """Run Scrapy crawler in async context using subprocess."""
    logger.info("Starting Stage 1: Scrapy URL Discovery")

    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'scrapy', 'crawl', 'scout',
        cwd=str(Path(__file__).parent),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    # Stream output in real-time
    async def log_stream(stream, prefix):
        async for line in stream:
            logger.info(f"[{prefix}] {line.decode().strip()}")

    await asyncio.gather(
        log_stream(process.stdout, "SCRAPY"),
        log_stream(process.stderr, "SCRAPY-ERR")
    )

    await process.wait()
    logger.info(f"Scrapy crawler finished with code {process.returncode}")


async def run_js_bot():
    """Run JavaScript rendering bot for JS-heavy pages."""
    from src.stage1.js_bot import run_js_bot

    logger.info("Starting Stage 1: JS Bot for JavaScript-heavy pages")
    try:
        await run_js_bot()
        logger.info("JS Bot completed successfully")
    except Exception as e:
        logger.error(f"JS Bot failed: {e}", exc_info=True)


async def run_stage2_workers(num_workers: int = 50, batch_size: int = 100):
    """Run Stage 2 analytics workers with high concurrency."""
    logger.info(f"Starting Stage 2: {num_workers} analytics workers")

    worker = Stage2Worker(max_concurrent=num_workers, batch_size=batch_size)

    try:
        await worker.run()
        logger.info("Stage 2 workers completed successfully")
    except Exception as e:
        logger.error(f"Stage 2 workers failed: {e}", exc_info=True)


async def run_stage3_workers(num_workers: int = 20, batch_size: int = 50):
    """Run Stage 3 similarity detection & summarization workers."""
    logger.info(f"Starting Stage 3: {num_workers} summarization workers")

    worker = Stage3Worker(max_concurrent=num_workers, batch_size=batch_size)

    try:
        await worker.run()
        logger.info("Stage 3 workers completed successfully")
    except Exception as e:
        logger.error(f"Stage 3 workers failed: {e}", exc_info=True)


async def orchestrate_pipeline(
    enable_scrapy: bool = True,
    enable_js_bot: bool = True,
    enable_stage2: bool = True,
    enable_stage3: bool = True,
    stage2_workers: int = 50,
    stage3_workers: int = 20
):
    """
    Orchestrate the entire async pipeline.

    Args:
        enable_scrapy: Run Scrapy crawler
        enable_js_bot: Run JS rendering bot
        enable_stage2: Run Stage 2 analytics
        enable_stage3: Run Stage 3 summarization
        stage2_workers: Number of concurrent Stage 2 workers
        stage3_workers: Number of concurrent Stage 3 workers
    """
    logger.info("=" * 80)
    logger.info("ASYNC PIPELINE ORCHESTRATOR STARTING")
    logger.info("=" * 80)

    tasks = []

    # Stage 1: Discovery (can run in parallel)
    if enable_scrapy:
        tasks.append(asyncio.create_task(run_scrapy_crawler(), name="scrapy"))

    if enable_js_bot:
        tasks.append(asyncio.create_task(run_js_bot(), name="js-bot"))

    # Wait for Stage 1 to complete before starting Stage 2
    if tasks:
        logger.info("Waiting for Stage 1 (Discovery) to complete...")
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Stage 1 completed")

    # Stage 2: Analytics & Quality Control
    if enable_stage2:
        logger.info("Starting Stage 2 (Analytics & QC)...")
        await run_stage2_workers(num_workers=stage2_workers)
        logger.info("Stage 2 completed")

    # Stage 3: Similarity & Summarization
    if enable_stage3:
        logger.info("Starting Stage 3 (Similarity & Summarization)...")
        await run_stage3_workers(num_workers=stage3_workers)
        logger.info("Stage 3 completed")

    logger.info("=" * 80)
    logger.info("PIPELINE COMPLETED SUCCESSFULLY")
    logger.info("=" * 80)


def main():
    """Main entry point."""
    try:
        asyncio.run(orchestrate_pipeline())
    except KeyboardInterrupt:
        logger.warning("Pipeline interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
