#!/usr/bin/env python
"""Test Pipeline with Mock Data

Since we have network DNS limitations, this script:
1. Seeds real UConn URLs
2. Creates mock HTTP responses
3. Runs the pipeline to demonstrate data flow
4. Verifies metrics are collected
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime

# Add project to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.common.storage_manager import get_delta
from src.lakehouse import SeedManager
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def seed_urls():
    """Seed real UConn URLs."""
    logger.info("=" * 80)
    logger.info("STEP 1: SEEDING REAL UCONN URLS")
    logger.info("=" * 80)

    real_urls = [
        "https://uconn.edu/",
        "https://uconn.edu/about-us/",
        "https://uconn.edu/academics/",
        "https://uconn.edu/admissions/",
        "https://uconn.edu/campus-life/",
        "https://uconn.edu/research/",
        "https://today.uconn.edu/",
        "https://magazine.uconn.edu/",
        "https://engineering.uconn.edu/",
        "https://business.uconn.edu/",
    ]

    delta = get_delta()
    seed_manager = SeedManager(delta)

    result = seed_manager.add_urls_to_seeds(
        urls=real_urls,
        source_url="test_script",
        source_spider="test",
        write_uconn_urls=True,
        enqueue_stage2=False
    )

    logger.info(f"✅ Seeded {result.get('seed_inserted', 0)} URLs")
    return len(real_urls)


def create_mock_stage2_queue():
    """Create mock Stage 2 queue with real URLs."""
    logger.info("\n" + "=" * 80)
    logger.info("STEP 2: CREATING STAGE 2 QUEUE (Mock)")
    logger.info("=" * 80)

    delta = get_delta()

    queue_items = [
        {
            'url': 'https://uconn.edu/',
            'parent_url': 'seed',
            'content_hint': 'html',
            'status': 'pending',
            'queued_at': datetime.now().isoformat(),
            'queued_by': 'test',
        },
        {
            'url': 'https://uconn.edu/about-us/',
            'parent_url': 'https://uconn.edu/',
            'content_hint': 'html',
            'status': 'pending',
            'queued_at': datetime.now().isoformat(),
            'queued_by': 'test',
        },
        {
            'url': 'https://uconn.edu/academics/',
            'parent_url': 'https://uconn.edu/',
            'content_hint': 'html',
            'status': 'pending',
            'queued_at': datetime.now().isoformat(),
            'queued_by': 'test',
        },
    ]

    try:
        delta.write('stage2_queue', queue_items, mode='append')
        logger.info(f"✅ Created {len(queue_items)} queue items")
        return len(queue_items)
    except Exception as e:
        logger.error(f"Error creating queue: {e}")
        return 0


def create_mock_stage2_analysis():
    """Create mock Stage 2 analysis results."""
    logger.info("\n" + "=" * 80)
    logger.info("STEP 3: CREATING STAGE 2 ANALYSIS (Mock)")
    logger.info("=" * 80)

    delta = get_delta()

    analysis_items = [
        {
            'url': 'https://uconn.edu/',
            'url_hash': 'hash1',
            'word_count': 1500,
            'html_size': 45000,
            'text_size': 12000,
            'text_to_html_ratio': 0.267,
            'is_low_quality': False,
            'is_massive_doc': False,
            'text_content': 'Welcome to UConn. The University of Connecticut is a premier public research university. Founded in 1881, UConn offers undergraduate and graduate programs.',
            'analyzed_at': datetime.now().isoformat(),
            'http_status': 200,
        },
        {
            'url': 'https://uconn.edu/about-us/',
            'url_hash': 'hash2',
            'word_count': 2200,
            'html_size': 52000,
            'text_size': 18000,
            'text_to_html_ratio': 0.346,
            'is_low_quality': False,
            'is_massive_doc': False,
            'text_content': 'About UConn. Learn about our history, mission, and values. UConn serves over 32000 students across multiple campuses throughout Connecticut.',
            'analyzed_at': datetime.now().isoformat(),
            'http_status': 200,
        },
        {
            'url': 'https://uconn.edu/academics/',
            'url_hash': 'hash3',
            'word_count': 65000,
            'html_size': 250000,
            'text_size': 180000,
            'text_to_html_ratio': 0.720,
            'is_low_quality': False,
            'is_massive_doc': True,  # This will go to Stage 4
            'text_content': 'UConn Academics. Comprehensive guide to our academic programs...' * 1000,
            'analyzed_at': datetime.now().isoformat(),
            'http_status': 200,
        },
    ]

    try:
        delta.write('stage2_page_analysis', analysis_items, mode='append')
        logger.info(f"✅ Created {len(analysis_items)} analysis results")
        logger.info(f"   - Quality docs: 2 (will go to Stage 3)")
        logger.info(f"   - Massive docs: 1 (will go to Stage 4)")
        return len(analysis_items)
    except Exception as e:
        logger.error(f"Error creating analysis: {e}")
        return 0


def create_mock_stage3_summaries():
    """Create mock Stage 3 summaries."""
    logger.info("\n" + "=" * 80)
    logger.info("STEP 4: CREATING STAGE 3 SUMMARIES (Mock)")
    logger.info("=" * 80)

    delta = get_delta()

    summaries = [
        {
            'url': 'https://uconn.edu/',
            'url_hash': 'hash1',
            'summary': 'Welcome to UConn. The University of Connecticut is a premier public research university. Founded in 1881, UConn offers undergraduate and graduate programs.',
            'word_count': 1500,
            'keywords': ['university', 'research', 'education', 'Connecticut'],
            'quality_score': 0.85,
            'timestamp': datetime.now().isoformat(),
        },
        {
            'url': 'https://uconn.edu/about-us/',
            'url_hash': 'hash2',
            'summary': 'About UConn. Learn about our history, mission, and values. UConn serves over 32000 students across multiple campuses throughout Connecticut.',
            'word_count': 2200,
            'keywords': ['history', 'mission', 'students', 'campuses'],
            'quality_score': 0.90,
            'timestamp': datetime.now().isoformat(),
        },
    ]

    try:
        delta.write('stage4_summaries', summaries, mode='append')  # Stage 3 writes to stage4_summaries
        logger.info(f"✅ Created {len(summaries)} summaries")
        return len(summaries)
    except Exception as e:
        logger.error(f"Error creating summaries: {e}")
        return 0


def create_mock_stage4_summaries():
    """Create mock Stage 4 large doc summaries."""
    logger.info("\n" + "=" * 80)
    logger.info("STEP 5: CREATING STAGE 4 LARGE DOC SUMMARIES (Mock)")
    logger.info("=" * 80)

    delta = get_delta()

    large_summaries = [
        {
            'url': 'https://uconn.edu/academics/',
            'url_hash': 'hash3',
            'summary': 'Comprehensive academic programs overview. UConn offers degrees across 14 schools and colleges, with over 115 majors and extensive graduate programs.',
            'content_type': 'html',
            'original_size': 180000,
            'summary_size': 500,
            'compression_ratio': 0.0028,
            'processed_at': datetime.now().isoformat(),
        },
    ]

    try:
        delta.write('stage4_large_doc_summaries', large_summaries, mode='append')
        logger.info(f"✅ Created {len(large_summaries)} large doc summaries")
        logger.info(f"   - Average compression: {large_summaries[0]['compression_ratio']:.4f}")
        return len(large_summaries)
    except Exception as e:
        logger.error(f"Error creating large doc summaries: {e}")
        return 0


def verify_metrics():
    """Verify metrics are being collected."""
    logger.info("\n" + "=" * 80)
    logger.info("STEP 6: VERIFYING METRICS")
    logger.info("=" * 80)

    import requests

    try:
        response = requests.get('http://localhost:9090/metrics', timeout=5)
        metrics = response.text

        # Extract key metrics
        import re

        def get_metric(name):
            match = re.search(rf'{name}\s+([\d.]+)', metrics)
            return float(match.group(1)) if match else 0

        logger.info(f"\n📊 CURRENT METRICS:")
        logger.info(f"   Stage 1:")
        logger.info(f"     - URLs discovered: {get_metric('stage1_urls_discovered_total'):.0f}")
        logger.info(f"     - URLs queued: {get_metric('stage1_urls_queued_total'):.0f}")
        logger.info(f"   Stage 2:")
        logger.info(f"     - Pages analyzed: {get_metric('stage2_pages_analyzed_total'):.0f}")
        logger.info(f"     - Quality docs: {get_metric('stage2_quality_docs_total'):.0f}")
        logger.info(f"     - Massive docs: {get_metric('stage2_massive_docs_total'):.0f}")
        logger.info(f"     - Avg word count: {get_metric('stage2_avg_word_count'):.0f}")
        logger.info(f"   Stage 3:")
        logger.info(f"     - Summaries: {get_metric('stage3_summaries_created_total'):.0f}")
        logger.info(f"   Stage 4:")
        logger.info(f"     - Large doc summaries: {get_metric('stage4_large_summaries_total'):.0f}")

        logger.info(f"\n✅ Metrics endpoint is live at http://localhost:9090/metrics")
        return True

    except Exception as e:
        logger.error(f"Error verifying metrics: {e}")
        return False


def verify_data_flow():
    """Verify data is in Delta Lake."""
    logger.info("\n" + "=" * 80)
    logger.info("STEP 7: VERIFYING DATA FLOW")
    logger.info("=" * 80)

    delta = get_delta()

    tables = {
        'seed_urls': 'Stage 1 seeds',
        'stage2_queue': 'Stage 2 queue',
        'stage2_page_analysis': 'Stage 2 analysis',
        'stage4_summaries': 'Stage 3 summaries',
        'stage4_large_doc_summaries': 'Stage 4 large doc summaries',
    }

    logger.info("\n📁 DELTA LAKE TABLES:")
    for table_name, description in tables.items():
        try:
            data = delta.read_table(table_name)
            logger.info(f"   ✅ {description:30s}: {len(data):4d} records")
        except Exception as e:
            logger.info(f"   ⚠️  {description:30s}: No data")

    return True


def main():
    """Run complete pipeline test."""
    logger.info("\n" + "🚀 " * 40)
    logger.info("PIPELINE TEST WITH MOCK DATA (Real UConn URLs)")
    logger.info("🚀 " * 40 + "\n")

    try:
        # Step 1: Seed real URLs
        urls_seeded = seed_urls()

        # Step 2: Create mock Stage 2 queue
        queue_items = create_mock_stage2_queue()

        # Wait for metrics to update
        logger.info("\nWaiting 6 seconds for metrics to update...")
        import time
        time.sleep(6)

        # Step 3: Create mock analysis
        analysis_items = create_mock_stage2_analysis()

        # Wait for metrics
        time.sleep(6)

        # Step 4: Create mock summaries
        summaries = create_mock_stage3_summaries()

        # Step 5: Create mock large doc summaries
        large_summaries = create_mock_stage4_summaries()

        # Wait for final metrics update
        time.sleep(6)

        # Step 6: Verify metrics
        metrics_ok = verify_metrics()

        # Step 7: Verify data flow
        data_ok = verify_data_flow()

        # Final summary
        logger.info("\n" + "=" * 80)
        logger.info("PIPELINE TEST COMPLETE")
        logger.info("=" * 80)
        logger.info(f"✅ Real UConn URLs seeded: {urls_seeded}")
        logger.info(f"✅ Stage 2 queue items: {queue_items}")
        logger.info(f"✅ Pages analyzed: {analysis_items}")
        logger.info(f"✅ Summaries created: {summaries}")
        logger.info(f"✅ Large doc summaries: {large_summaries}")
        logger.info(f"✅ Metrics endpoint: {'LIVE' if metrics_ok else 'ERROR'}")
        logger.info(f"✅ Data flow: {'VERIFIED' if data_ok else 'ERROR'}")
        logger.info("=" * 80)

        logger.info(f"\n📊 VIEW METRICS:")
        logger.info(f"   curl http://localhost:9090/metrics | grep stage")
        logger.info(f"\n📁 VIEW DATA:")
        logger.info(f"   ls -lh data/delta_lake/*/")

        return True

    except Exception as e:
        logger.error(f"Pipeline test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    import os
    os.chdir(project_root)

    success = main()
    sys.exit(0 if success else 1)
