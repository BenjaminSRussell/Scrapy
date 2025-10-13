#!/usr/bin/env python3
"""Vacuum Delta Lake tables to remove old, unreferenced data files.

This script should be run periodically (e.g., via cron) to clean up old
Parquet files that are no longer referenced by the Delta transaction log.

Usage:
    # Vacuum with default 7-day retention
    python scripts/vacuum_delta_tables.py

    # Vacuum with custom retention (in hours)
    python scripts/vacuum_delta_tables.py --retention-hours 336

    # Vacuum specific tables only
    python scripts/vacuum_delta_tables.py --tables stage1_discovery stage2_page_analysis

Example cron job (run weekly on Sunday at 2 AM):
    0 2 * * 0 cd /path/to/project && /path/to/.venv/bin/python scripts/vacuum_delta_tables.py
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.common.delta_lake import get_delta_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description='Vacuum Delta Lake tables to remove old data files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        '--retention-hours',
        type=int,
        default=168,
        help='Retention period in hours (default: 168 = 7 days)'
    )
    parser.add_argument(
        '--tables',
        nargs='*',
        help='Specific tables to vacuum (default: all tables)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be vacuumed without actually doing it'
    )
    parser.add_argument(
        '--unsafe',
        action='store_true',
        help='Allow retention < 168 hours (DANGEROUS - disables safety checks)'
    )

    args = parser.parse_args()

    logger.info("=" * 70)
    logger.info("Delta Lake Vacuum Script")
    logger.info("=" * 70)
    logger.info(f"Retention period: {args.retention_hours} hours ({args.retention_hours / 24:.1f} days)")

    if args.dry_run:
        logger.info("DRY RUN MODE - No files will be deleted")

    try:
        # Initialize Delta Lake manager
        manager = get_delta_manager()

        # Get list of tables to vacuum
        if args.tables:
            tables_to_vacuum = args.tables
            logger.info(f"Vacuuming specific tables: {', '.join(tables_to_vacuum)}")
        else:
            tables_to_vacuum = list(manager.tables.keys())
            logger.info("Vacuuming all tables")

        # Vacuum each table
        vacuumed_count = 0
        skipped_count = 0

        for table_name in tables_to_vacuum:
            if table_name not in manager.tables:
                logger.warning(f"Unknown table: {table_name} - skipping")
                skipped_count += 1
                continue

            table_path = manager.tables[table_name]
            if not (table_path / "_delta_log").exists():
                logger.info(f"Table {table_name} has no data - skipping")
                skipped_count += 1
                continue

            if args.dry_run:
                logger.info(f"Would vacuum {table_name}")
                vacuumed_count += 1
            else:
                try:
                    logger.info(f"Vacuuming {table_name}...")
                    enforce_retention = not args.unsafe
                    manager._vacuum_table(table_name, args.retention_hours, enforce_retention_duration=enforce_retention)
                    vacuumed_count += 1
                except Exception as e:
                    logger.error(f"Failed to vacuum {table_name}: {e}", exc_info=True)
                    skipped_count += 1

        logger.info("=" * 70)
        logger.info(f"✅ Vacuum complete!")
        logger.info(f"   Tables vacuumed: {vacuumed_count}")
        logger.info(f"   Tables skipped: {skipped_count}")
        logger.info("=" * 70)

        return 0

    except Exception as e:
        logger.error(f"Vacuum script failed: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
