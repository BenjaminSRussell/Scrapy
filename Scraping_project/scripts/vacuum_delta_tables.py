#!/usr/bin/env python3

import argparse
import logging
import sys
from importlib import import_module
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

def _ensure_project_root() -> None:
    project_root = str(PROJECT_ROOT)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

def _get_delta_manager():
    _ensure_project_root()
    delta_module = import_module("src.common.delta_lake")
    return delta_module.get_delta_manager()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(
        description="Vacuum Delta Lake tables to remove old data files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--retention-hours",
        type=int,
        default=168,
        help="Retention period in hours (default: 168 = 7 days)",
    )
    parser.add_argument("--tables", nargs="*", help="Specific tables to vacuum (default: all tables)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be vacuumed without actually doing it",
    )
    parser.add_argument(
        "--unsafe",
        action="store_true",
        help="Allow retention < 168 hours (DANGEROUS - disables safety checks)",
    )

    args = parser.parse_args()

    logger.info("=" * 70)
    logger.info("Delta Lake Vacuum Script")
    logger.info("=" * 70)
    logger.info(f"Retention period: {args.retention_hours} hours ({args.retention_hours / 24:.1f} days)")

    if args.dry_run:
        logger.info("DRY RUN MODE - No files will be deleted")

    try:
        manager = _get_delta_manager()

        if args.tables:
            tables_to_vacuum = args.tables
            logger.info(f"Vacuuming specific tables: {', '.join(tables_to_vacuum)}")
        else:
            tables_to_vacuum = list(manager.tables.keys())
            logger.info("Vacuuming all tables")

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
                    manager._vacuum_table(
                        table_name,
                        args.retention_hours,
                        enforce_retention_duration=enforce_retention,
                    )
                    vacuumed_count += 1
                except Exception as e:
                    logger.error(f"Failed to vacuum {table_name}: {e}", exc_info=True)
                    skipped_count += 1

        logger.info("=" * 70)
        logger.info("✅ Vacuum complete!")
        logger.info(f"   Tables vacuumed: {vacuumed_count}")
        logger.info(f"   Tables skipped: {skipped_count}")
        logger.info("=" * 70)

        return 0

    except Exception as e:
        logger.error(f"Vacuum script failed: {e}", exc_info=True)
        return 1

if __name__ == "__main__":
    sys.exit(main())
