#!/usr/bin/env python3
"""
UConn Web Scraping Pipeline - Main Entry Point

Orchestrates the complete scraping pipeline with Delta Lake storage.

Usage:
    python start.py --full              # Run complete pipeline
    python start.py --stage1            # Run stage 1 only
    python start.py --stage2            # Run stage 2 only
    python start.py --stage3            # Run stage 3 only
    python start.py --query "SQL"       # Query Delta Lake
    python start.py --status            # Show pipeline status
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from src.common.constants import DELTA_ENRICHED_CONTENT, DELTA_RAW_URLS, DELTA_VALIDATED_URLS, LOGS_DIR
from src.common.delta_lake import DeltaLakeReader
from src.common.logging import get_logger, setup_logging

logger = get_logger(__name__)


def run_stage1(config_file: str = "config/development.yml"):
    """Run Stage 1: URL Discovery."""
    logger.info("=== STAGE 1: URL DISCOVERY ===")

    from src.orchestrator.config import Config
    config = Config(config_file)

    # Run discovery spider
    import subprocess
    result = subprocess.run(
        ["python", "-m", "scrapy", "crawl", "discovery_spider"],
        cwd=Path(__file__).parent / "Scraping_project",
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        logger.error(f"Stage 1 failed: {result.stderr}")
        return False

    logger.info("Stage 1 completed successfully")
    return True


def run_stage2(config_file: str = "config/development.yml"):
    """Run Stage 2: URL Validation with file type detection."""
    logger.info("=== STAGE 2: URL VALIDATION ===")

    from src.orchestrator.config import Config
    from src.stage2.validator import URLValidator

    config = Config(config_file)
    validator = URLValidator(config)

    # Run validation
    asyncio.run(validator.validate_all())

    logger.info("Stage 2 completed successfully")
    return True


def run_stage3(config_file: str = "config/development.yml"):
    """Run Stage 3: Content Enrichment with OCR/Whisper."""
    logger.info("=== STAGE 3: CONTENT ENRICHMENT ===")


    # Run async enrichment
    result = subprocess.run(
        ["python", "-m", "src.stage3.async_enrichment"],
        cwd=Path(__file__).parent / "Scraping_project",
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        logger.error(f"Stage 3 failed: {result.stderr}")
        return False

    logger.info("Stage 3 completed successfully")
    return True


def run_full_pipeline(config_file: str = "config/development.yml"):
    """Run complete pipeline: Stage 1 -> 2 -> 3."""
    logger.info("=== STARTING FULL PIPELINE ===")
    start_time = datetime.now()

    stages = [
        ("Stage 1: Discovery", run_stage1),
        ("Stage 2: Validation", run_stage2),
        ("Stage 3: Enrichment", run_stage3)
    ]

    for stage_name, stage_func in stages:
        logger.info(f"\n{'=' * 60}\n{stage_name}\n{'=' * 60}")

        if not stage_func(config_file):
            logger.error(f"{stage_name} failed, pipeline aborted")
            return False

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"\n{'=' * 60}\nPIPELINE COMPLETED in {elapsed:.1f}s\n{'=' * 60}")

    # Show summary
    show_status()

    return True


def query_delta_lake(sql: str):
    """Query Delta Lake using SQL."""
    logger.info(f"Executing query: {sql}")

    try:
        from src.common.delta_lake import DeltaLakeReader

        # Determine which table to query (simple heuristic)
        if 'enriched' in sql.lower():
            reader = DeltaLakeReader(DELTA_ENRICHED_CONTENT)
        elif 'validated' in sql.lower():
            reader = DeltaLakeReader(DELTA_VALIDATED_URLS)
        else:
            reader = DeltaLakeReader(DELTA_RAW_URLS)

        results = reader.query(sql)

        print(f"\nFound {len(results)} results:")
        for i, row in enumerate(results[:10], 1):
            print(f"\n{i}. {json.dumps(row, indent=2)}")

        if len(results) > 10:
            print(f"\n... and {len(results) - 10} more")

    except Exception as e:
        logger.error(f"Query failed: {e}")


def show_status():
    """Show pipeline status and statistics."""
    print("\n" + "=" * 60)
    print("PIPELINE STATUS")
    print("=" * 60)

    try:
        # Raw URLs
        try:
            reader = DeltaLakeReader(DELTA_RAW_URLS)
            raw_count = reader.count()
            print(f"\n✓ Raw URLs discovered: {raw_count:,}")
        except:
            print("\n✗ Raw URLs: Not available")

        # Validated URLs
        try:
            reader = DeltaLakeReader(DELTA_VALIDATED_URLS)
            validated_count = reader.count()
            print(f"✓ URLs validated: {validated_count:,}")
        except:
            print("✗ Validated URLs: Not available")

        # Enriched Content
        try:
            reader = DeltaLakeReader(DELTA_ENRICHED_CONTENT)
            enriched_count = reader.count()
            print(f"✓ Content enriched: {enriched_count:,}")

            # Sample recent entries
            recent = reader.read(columns=['url', 'title', '_ingestion_time'])[-5:]
            print("\nRecent enriched content:")
            for entry in recent:
                print(f"  • {entry.get('title', 'No title')[:50]}")
                print(f"    {entry.get('url', '')}")

        except:
            print("✗ Enriched Content: Not available")

    except Exception as e:
        print(f"\nError getting status: {e}")

    print("\n" + "=" * 60)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="UConn Web Scraping Pipeline with Delta Lake",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python start.py --full                    # Run complete pipeline
  python start.py --stage1                  # Run discovery only
  python start.py --query "title LIKE '%admissions%'"  # Query Delta Lake
  python start.py --status                  # Show pipeline status
        """
    )

    parser.add_argument('--full', action='store_true', help='Run full pipeline')
    parser.add_argument('--stage1', action='store_true', help='Run Stage 1 (Discovery)')
    parser.add_argument('--stage2', action='store_true', help='Run Stage 2 (Validation)')
    parser.add_argument('--stage3', action='store_true', help='Run Stage 3 (Enrichment)')
    parser.add_argument('--query', type=str, help='Query Delta Lake (SQL WHERE clause)')
    parser.add_argument('--status', action='store_true', help='Show pipeline status')
    parser.add_argument('--config', default='config/development.yml', help='Config file path')
    parser.add_argument('--log-level', default='INFO', help='Logging level')

    args = parser.parse_args()

    # Setup logging
    setup_logging(log_level=args.log_level, log_dir=LOGS_DIR)

    # Execute requested action
    try:
        if args.full:
            success = run_full_pipeline(args.config)
            sys.exit(0 if success else 1)

        elif args.stage1:
            success = run_stage1(args.config)
            sys.exit(0 if success else 1)

        elif args.stage2:
            success = run_stage2(args.config)
            sys.exit(0 if success else 1)

        elif args.stage3:
            success = run_stage3(args.config)
            sys.exit(0 if success else 1)

        elif args.query:
            query_delta_lake(args.query)

        elif args.status:
            show_status()

        else:
            parser.print_help()
            sys.exit(1)

    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        sys.exit(0)

    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
