#!/usr/bin/env python3
"""Test Pipeline Stages - Run each stage and identify all issues

This script tests:
- Stage 1: Scout Spider (URL Discovery)
- Stage 2: Page Analysis
- Stage 3: Summarization
- Stage 4: Large Document Processing
"""

import asyncio
import logging
import sys
import traceback
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Track all issues found
ISSUES = []


def log_issue(stage: str, issue: str, error: Exception = None):
    """Log an issue found during testing."""
    issue_msg = f"[{stage}] {issue}"
    if error:
        issue_msg += f"\n  Error: {error}"
        issue_msg += f"\n  Type: {type(error).__name__}"

    ISSUES.append(issue_msg)
    logger.error(issue_msg)


def test_imports():
    """Test all critical imports."""
    logger.info("=" * 80)
    logger.info("TESTING IMPORTS")
    logger.info("=" * 80)

    imports = [
        ("scrapy", "Scrapy framework"),
        ("deltalake", "Delta Lake"),
        ("pyarrow", "PyArrow"),
        ("pandas", "Pandas"),
        ("httpx", "HTTPX"),
        ("aiohttp", "Aiohttp"),
        ("bs4", "BeautifulSoup4"),
        ("lxml", "LXML"),
        ("datasketch", "DataSketch (for Stage 3)"),
        ("yake", "YAKE (keyword extraction)"),
        ("transformers", "Transformers (for Stage 4)"),
        ("torch", "PyTorch (for Stage 4)"),
        ("easyocr", "EasyOCR (optional)"),
        ("PIL", "Pillow"),
        ("duckdb", "DuckDB (for exports)"),
    ]

    for module_name, description in imports:
        try:
            __import__(module_name)
            logger.info(f"  ✅ {description}")
        except ImportError as e:
            log_issue("IMPORTS", f"Missing {description}: {module_name}", e)
            logger.error(f"  ❌ {description} - {e}")


def test_stage1_imports():
    """Test Stage 1 imports."""
    logger.info("\n" + "=" * 80)
    logger.info("TESTING STAGE 1: Scout Spider")
    logger.info("=" * 80)

    try:
        from src.stage1.scout_spider import ScoutSpider
        logger.info("  ✅ ScoutSpider imported successfully")

        # Check settings
        settings = ScoutSpider.custom_settings
        logger.info(f"  ✅ Concurrent requests: {settings['CONCURRENT_REQUESTS']}")
        logger.info(f"  ✅ Per-domain requests: {settings['CONCURRENT_REQUESTS_PER_DOMAIN']}")

    except Exception as e:
        log_issue("STAGE1", "Failed to import ScoutSpider", e)
        traceback.print_exc()


def test_stage1_ultra_discovery():
    """Test UltraDiscovery module."""
    try:
        from src.stage1.ultra_discovery import UltraDiscovery
        logger.info("  ✅ UltraDiscovery imported successfully")
    except Exception as e:
        log_issue("STAGE1", "Failed to import UltraDiscovery", e)


def test_stage2_imports():
    """Test Stage 2 imports."""
    logger.info("\n" + "=" * 80)
    logger.info("TESTING STAGE 2: Page Analysis")
    logger.info("=" * 80)

    try:
        from src.stage2.stage2_worker import Stage2Worker
        logger.info("  ✅ Stage2Worker imported successfully")

        # Try to instantiate
        worker = Stage2Worker(max_concurrent=5, batch_size=10)
        logger.info(f"  ✅ Stage2Worker instantiated: {worker.max_concurrent} workers")

    except Exception as e:
        log_issue("STAGE2", "Failed to import/instantiate Stage2Worker", e)
        traceback.print_exc()


def test_stage2_intelligent_analyzer():
    """Test Intelligent Analyzer."""
    try:
        from src.stage2.intelligent_analyzer import IntelligentAnalyzer
        logger.info("  ✅ IntelligentAnalyzer imported successfully")

        # Try to instantiate
        analyzer = IntelligentAnalyzer()
        logger.info(f"  ✅ IntelligentAnalyzer instantiated")
        analyzer.client.close()

    except Exception as e:
        log_issue("STAGE2", "Failed to import/instantiate IntelligentAnalyzer", e)
        traceback.print_exc()


def test_stage3_imports():
    """Test Stage 3 imports."""
    logger.info("\n" + "=" * 80)
    logger.info("TESTING STAGE 3: Summarization")
    logger.info("=" * 80)

    try:
        from src.stage3.stage3_worker import Stage3Worker
        logger.info("  ✅ Stage3Worker imported successfully")

        # Try to instantiate
        worker = Stage3Worker(max_concurrent=5, batch_size=10)
        logger.info(f"  ✅ Stage3Worker instantiated: {worker.max_concurrent} workers")

    except Exception as e:
        log_issue("STAGE3", "Failed to import/instantiate Stage3Worker", e)
        traceback.print_exc()


def test_stage4_imports():
    """Test Stage 4 imports."""
    logger.info("\n" + "=" * 80)
    logger.info("TESTING STAGE 4: Large Document Processing")
    logger.info("=" * 80)

    # Check if Stage 4 files exist
    stage4_files = [
        "src/stage4/summarization.py",
        "src/stage4/large_doc_processor.py",
    ]

    for file_path in stage4_files:
        full_path = Path(file_path)
        if full_path.exists():
            logger.info(f"  ✅ Found: {file_path}")
        else:
            log_issue("STAGE4", f"Missing file: {file_path}")

    try:
        from src.stage4.summarization import summarize_with_heavy_model
        logger.info("  ✅ Summarization module imported")
    except Exception as e:
        log_issue("STAGE4", "Failed to import summarization module", e)
        traceback.print_exc()

    try:
        from src.stage4.large_doc_processor import LargeDocProcessor
        logger.info("  ✅ LargeDocProcessor imported")
    except Exception as e:
        log_issue("STAGE4", "Failed to import LargeDocProcessor", e)
        traceback.print_exc()


def test_delta_lake():
    """Test Delta Lake manager."""
    logger.info("\n" + "=" * 80)
    logger.info("TESTING DELTA LAKE")
    logger.info("=" * 80)

    try:
        from src.common.delta_lake import get_delta_manager
        delta = get_delta_manager()
        logger.info("  ✅ Delta Lake manager initialized")

        # Check tables
        logger.info(f"  ✅ Configured tables: {list(delta.tables.keys())}")

        # Try a test write (small)
        test_data = [{'test': 'value', 'number': 123}]
        delta.write('stage1_discovery', test_data, mode='append', async_write=False)
        logger.info("  ✅ Test write successful")

        # Try a read
        data = delta.read('stage1_discovery')
        logger.info(f"  ✅ Test read successful: {len(data)} records")

    except Exception as e:
        log_issue("DELTA_LAKE", "Failed to initialize or use Delta Lake", e)
        traceback.print_exc()


def test_export_utility():
    """Test export utility."""
    logger.info("\n" + "=" * 80)
    logger.info("TESTING EXPORT UTILITY")
    logger.info("=" * 80)

    try:
        import duckdb
        logger.info("  ✅ DuckDB available")

        # Try to list tables
        import sys
        sys.path.insert(0, str(Path(__file__).parent))

        # Just check if the script exists
        export_script = Path("export_table.py")
        if export_script.exists():
            logger.info(f"  ✅ Export script exists: {export_script}")
        else:
            log_issue("EXPORT", "export_table.py not found")

    except ImportError as e:
        log_issue("EXPORT", "DuckDB not installed - export will not work", e)


async def test_stage2_worker():
    """Test Stage 2 worker with a real URL."""
    logger.info("\n" + "=" * 80)
    logger.info("TESTING STAGE 2 WORKER (REAL URL)")
    logger.info("=" * 80)

    try:
        from src.stage2.stage2_worker import Stage2Worker

        worker = Stage2Worker(max_concurrent=1, batch_size=1)

        # Test with a simple URL record
        test_record = {
            'url': 'https://example.com',
            'url_hash': 'test123',
            'depth': 0,
            'status_code': 200,
            'content_type': 'text/html',
            'content_size': 1000,
            'is_heavy': False,
            'requires_js': False,
        }

        logger.info(f"  Testing analysis of: {test_record['url']}")
        result = await worker._analyze_url(test_record)

        if isinstance(result, dict) and not isinstance(result, Exception):
            logger.info(f"  ✅ Stage 2 analysis successful")
            logger.info(f"     Quality score: {result.get('quality_score', 'N/A')}")
            logger.info(f"     Word count: {result.get('word_count', 'N/A')}")
        else:
            log_issue("STAGE2", f"Stage 2 worker returned error: {result}")

    except Exception as e:
        log_issue("STAGE2", "Failed to run Stage 2 worker", e)
        traceback.print_exc()


async def test_stage3_worker():
    """Test Stage 3 worker."""
    logger.info("\n" + "=" * 80)
    logger.info("TESTING STAGE 3 WORKER")
    logger.info("=" * 80)

    try:
        from src.stage3.stage3_worker import Stage3Worker

        worker = Stage3Worker(max_concurrent=1, batch_size=1)

        # Test document
        test_doc = {
            'url': 'https://example.com',
            'url_hash': 'test123',
            'text_content': 'This is a test document with some content. It has multiple sentences. The content should be analyzed and summarized.',
            'word_count': 100,
            'quality_score': 0.8,
            'keywords': ['test', 'document', 'content'],
        }

        logger.info(f"  Testing summarization of test document")
        result = await worker._process_document(test_doc)

        if isinstance(result, dict) and 'summary' in result:
            logger.info(f"  ✅ Stage 3 summarization successful")
            logger.info(f"     Summary length: {len(result.get('summary', ''))}")
        else:
            log_issue("STAGE3", f"Stage 3 worker failed: {result}")

    except Exception as e:
        log_issue("STAGE3", "Failed to run Stage 3 worker", e)
        traceback.print_exc()


def test_seed_file():
    """Test seed file."""
    logger.info("\n" + "=" * 80)
    logger.info("TESTING SEED FILE")
    logger.info("=" * 80)

    seed_file = Path("data/raw/uconn_urls.csv")

    if not seed_file.exists():
        log_issue("SEED", f"Seed file not found: {seed_file}")
        return

    try:
        with open(seed_file) as f:
            urls = [line.strip() for line in f if line.strip() and line.strip().startswith('http')]

        logger.info(f"  ✅ Seed file exists: {len(urls):,} URLs")
        logger.info(f"  ✅ First URL: {urls[0] if urls else 'N/A'}")
        logger.info(f"  ✅ Last URL: {urls[-1] if urls else 'N/A'}")

    except Exception as e:
        log_issue("SEED", "Failed to read seed file", e)


async def main():
    """Run all tests."""
    logger.info("🧪 STARTING COMPREHENSIVE PIPELINE TESTS\n")

    # Test imports first
    test_imports()

    # Test each stage
    test_stage1_imports()
    test_stage1_ultra_discovery()
    test_stage2_imports()
    test_stage2_intelligent_analyzer()
    test_stage3_imports()
    test_stage4_imports()

    # Test infrastructure
    test_delta_lake()
    test_export_utility()
    test_seed_file()

    # Test async workers
    await test_stage2_worker()
    await test_stage3_worker()

    # Print summary
    logger.info("\n" + "=" * 80)
    logger.info("TEST SUMMARY")
    logger.info("=" * 80)

    if ISSUES:
        logger.error(f"\n❌ FOUND {len(ISSUES)} ISSUES:\n")
        for i, issue in enumerate(ISSUES, 1):
            logger.error(f"{i}. {issue}\n")

        # Save issues to file
        issues_file = Path("test_issues.txt")
        with open(issues_file, 'w') as f:
            f.write("PIPELINE TESTING ISSUES\n")
            f.write("=" * 80 + "\n\n")
            for i, issue in enumerate(ISSUES, 1):
                f.write(f"{i}. {issue}\n\n")

        logger.info(f"📝 Issues saved to: {issues_file}")
        return 1
    else:
        logger.info("\n✅ ALL TESTS PASSED - NO ISSUES FOUND!\n")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
