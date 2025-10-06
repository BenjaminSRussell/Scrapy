#!/usr/bin/env python3
"""Test script to verify async pipeline without Scrapy."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from run_pipeline import orchestrate_pipeline


async def main():
    """Run pipeline without Scrapy for testing."""
    await orchestrate_pipeline(
        enable_scrapy=False,
        enable_js_bot=False,
        enable_stage2=True,
        enable_stage3=True,
        stage2_workers=10,
        stage3_workers=5,
        continuous_mode=False  # Sequential mode for testing
    )


if __name__ == "__main__":
    asyncio.run(main())
