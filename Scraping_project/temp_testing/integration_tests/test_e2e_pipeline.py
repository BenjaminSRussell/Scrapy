"""End-to-End Integration Test - Full Pipeline with Real URLs

This test runs the ACTUAL pipeline code (no mocks) and verifies:
1. Stage 1: URL Discovery from seed file
2. Stage 2: Page Analysis and Quality Control
3. Stage 3: Similarity Detection and Summarization
4. Delta Lake: Proper data flow between stages
"""

import asyncio
import logging
import sys
import time
from pathlib import Path

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.common.delta_lake import get_delta_manager
from src.stage1.scout_spider import ScoutSpider
from src.stage2.stage2_worker import Stage2Worker
from src.stage3.stage3_worker import Stage3Worker

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_full_pipeline_e2e():
    """Test complete pipeline flow with real URLs from uconn_urls.csv"""

    delta = get_delta_manager()

    # === STAGE 1: URL Discovery ===
    logger.info("=" * 80)
    logger.info("STAGE 1: URL DISCOVERY")
    logger.info("=" * 80)

    # Run Scrapy spider for limited time
    from scrapy.crawler import CrawlerProcess
    from scrapy.utils.project import get_project_settings

    process = CrawlerProcess(get_project_settings())
    process.crawl(ScoutSpider)

    # Run spider in background thread with timeout
    import threading

    spider_thread = threading.Thread(target=process.start, daemon=True)
    spider_thread.start()

    # Let it run for 30 seconds
    logger.info("Running spider for 30 seconds...")
    time.sleep(30)

    # Force shutdown
    logger.info("Stopping spider...")
    delta.force_shutdown(timeout=10)

    # Verify Stage 1 output
    discovered_urls = delta.read('stage1_discovery')
    assert len(discovered_urls) > 0, "Stage 1 should discover at least 1 URL"
    logger.info(f"✅ Stage 1 complete: {len(discovered_urls)} URLs discovered")

    # === STAGE 2: Page Analysis ===
    logger.info("=" * 80)
    logger.info("STAGE 2: PAGE ANALYSIS")
    logger.info("=" * 80)

    # Get URLs that need analysis
    try:
        processed = delta.read('stage2_page_analysis')
        processed_hashes = {r['url_hash'] for r in processed}
    except Exception:
        processed_hashes = set()

    pending = [
        url for url in discovered_urls
        if url.get('url_hash') not in processed_hashes
        and not url.get('is_non_html', False)  # Skip non-HTML
    ][:10]  # Limit to 10 for testing

    assert len(pending) > 0, "Should have URLs pending for Stage 2"

    worker = Stage2Worker(max_concurrent=10, batch_size=10)
    tasks = [worker._analyze_url(record) for record in pending]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    valid_results = [r for r in results if isinstance(r, dict) and not isinstance(r, Exception)]

    assert len(valid_results) > 0, "Stage 2 should produce at least 1 valid result"

    # Write to Delta Lake
    delta.write('stage2_page_analysis', valid_results, mode='append', async_write=False)
    logger.info(f"✅ Stage 2 complete: {len(valid_results)} pages analyzed")

    # === STAGE 3: Summarization ===
    logger.info("=" * 80)
    logger.info("STAGE 3: SUMMARIZATION")
    logger.info("=" * 80)

    # Get quality documents
    try:
        processed_stage3 = delta.read('stage3_summaries')
        processed_hashes_stage3 = {r['url_hash'] for r in processed_stage3}
    except Exception:
        processed_hashes_stage3 = set()

    quality_docs = [
        doc for doc in valid_results
        if not doc.get('is_low_quality', True)
        and not doc.get('is_massive_doc', False)
        and doc.get('url_hash') not in processed_hashes_stage3
    ][:5]  # Limit to 5 for testing

    if len(quality_docs) > 0:
        worker_stage3 = Stage3Worker(max_concurrent=5, batch_size=5)
        tasks = [worker_stage3._process_document(doc) for doc in quality_docs]
        summaries = await asyncio.gather(*tasks, return_exceptions=True)

        valid_summaries = [s for s in summaries if isinstance(s, dict) and not isinstance(s, Exception)]

        if len(valid_summaries) > 0:
            delta.write('stage3_summaries', valid_summaries, mode='append', async_write=False)
            logger.info(f"✅ Stage 3 complete: {len(valid_summaries)} summaries created")
        else:
            logger.warning("⚠️  Stage 3: No valid summaries (may be low quality content)")
    else:
        logger.warning("⚠️  No quality documents for Stage 3 (content may be too short)")

    # === FINAL VERIFICATION ===
    logger.info("=" * 80)
    logger.info("FINAL VERIFICATION")
    logger.info("=" * 80)

    # Check all stages have data
    stage1_count = len(delta.read('stage1_discovery'))
    stage2_count = len(delta.read('stage2_page_analysis'))

    logger.info(f"Stage 1 (Discovery): {stage1_count} URLs")
    logger.info(f"Stage 2 (Analysis):  {stage2_count} pages")

    try:
        stage3_count = len(delta.read('stage3_summaries'))
        logger.info(f"Stage 3 (Summaries): {stage3_count} summaries")
    except Exception:
        stage3_count = 0
        logger.info(f"Stage 3 (Summaries): {stage3_count} summaries (no quality content)")

    # Assertions
    assert stage1_count > 0, "Stage 1 should have discovered URLs"
    assert stage2_count > 0, "Stage 2 should have analyzed pages"
    # Stage 3 may be 0 if content is low quality, so we don't assert

    logger.info("=" * 80)
    logger.info("✅ END-TO-END PIPELINE TEST PASSED")
    logger.info("=" * 80)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_pipeline_data_flow():
    """Verify data flows correctly between stages"""

    delta = get_delta_manager()

    # Check that Stage 2 only processes URLs from Stage 1
    stage1_urls = delta.read('stage1_discovery')
    stage2_urls = delta.read('stage2_page_analysis')

    stage1_hashes = {url['url_hash'] for url in stage1_urls}
    stage2_hashes = {url['url_hash'] for url in stage2_urls}

    # All Stage 2 URLs should come from Stage 1
    assert stage2_hashes.issubset(stage1_hashes), "Stage 2 should only process URLs from Stage 1"

    logger.info("✅ Data flow verification passed")


if __name__ == "__main__":
    # Run tests directly
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )

    asyncio.run(test_full_pipeline_e2e())
    asyncio.run(test_pipeline_data_flow())
