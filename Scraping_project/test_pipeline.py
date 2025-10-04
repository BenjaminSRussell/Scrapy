#!/usr/bin/env python3
"""
Quick Pipeline Test

Tests the complete 3-stage pipeline with Delta Lake integration.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.common.logging import setup_logging, get_logger
from src.common.constants import LOGS_DIR, DELTA_RAW_URLS, DELTA_VALIDATED_URLS
from src.common.delta_lake import DeltaLakeReader
from src.stage1.simple_discovery import run_discovery
from src.stage2.file_validator import run_validation

logger = get_logger(__name__)


async def test_stage1():
    """Test Stage 1: URL Discovery."""
    logger.info("\n" + "=" * 60)
    logger.info("TESTING STAGE 1: URL DISCOVERY")
    logger.info("=" * 60)

    await run_discovery(
        seed_urls=['https://example.com'],
        allowed_domains=['example.com'],
        max_depth=2,
        max_urls=100,
        concurrency=16
    )

    # Verify results
    try:
        reader = DeltaLakeReader(DELTA_RAW_URLS)
        count = reader.count()
        logger.info(f"✓ Stage 1 Success: {count} URLs discovered")
        return True
    except Exception as e:
        logger.error(f"✗ Stage 1 Failed: {e}")
        return False


async def test_stage2():
    """Test Stage 2: File Validation."""
    logger.info("\n" + "=" * 60)
    logger.info("TESTING STAGE 2: FILE VALIDATION & PROCESSING")
    logger.info("=" * 60)

    await run_validation(
        concurrency=16,
        timeout=10,
        enable_ocr=False,  # Disable for quick test
        enable_whisper=False
    )

    # Verify results
    try:
        reader = DeltaLakeReader(DELTA_VALIDATED_URLS)
        count = reader.count()
        logger.info(f"✓ Stage 2 Success: {count} URLs validated")
        return True
    except Exception as e:
        logger.error(f"✗ Stage 2 Failed: {e}")
        return False


async def main():
    """Run pipeline test."""
    setup_logging(log_level='INFO', log_dir=LOGS_DIR)

    logger.info("\n" + "=" * 60)
    logger.info("PIPELINE TEST STARTING")
    logger.info("=" * 60)

    # Test Stage 1
    stage1_ok = await test_stage1()

    if not stage1_ok:
        logger.error("Stage 1 failed, aborting test")
        return False

    # Test Stage 2
    stage2_ok = await test_stage2()

    if not stage2_ok:
        logger.error("Stage 2 failed, aborting test")
        return False

    logger.info("\n" + "=" * 60)
    logger.info("✓ ALL TESTS PASSED")
    logger.info("=" * 60)

    return True


if __name__ == '__main__':
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
