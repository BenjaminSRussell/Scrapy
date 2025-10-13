#!/usr/bin/env python3
"""Emergency Delta Log Cleanup - Manually clean transaction logs

WARNING: This script is DANGEROUS and should only be used in emergency situations
where the Delta Lake transaction logs have become so bloated that operations hang.

What this does:
1. Creates a new checkpoint for each table
2. Optionally deletes old JSON log files older than the last checkpoint

This will break time-travel queries beyond the checkpoint!
Use with extreme caution.
"""

import argparse
import logging
import shutil
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.common.delta_lake import get_delta_manager
from deltalake import DeltaTable

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def force_checkpoint(table_path: Path, table_name: str):
    """Force creation of a checkpoint for a Delta table."""
    try:
        logger.info(f"Creating checkpoint for {table_name}...")
        dt = DeltaTable(str(table_path))

        # Get current version
        version = dt.version()
        logger.info(f"  Current version: {version}")

        # Create checkpoint
        dt.create_checkpoint()
        logger.info(f"  ✅ Checkpoint created for {table_name}")

        return True
    except Exception as e:
        logger.error(f"  ❌ Failed to create checkpoint for {table_name}: {e}")
        return False


def cleanup_old_logs(table_path: Path, table_name: str, keep_last_n: int = 100, dry_run: bool = False):
    """Delete old JSON transaction log files, keeping only the last N."""
    log_dir = table_path / "_delta_log"
    if not log_dir.exists():
        logger.warning(f"  No log directory for {table_name}")
        return 0

    # Get all JSON files (transaction logs)
    json_files = sorted(log_dir.glob("*.json"))

    if len(json_files) <= keep_last_n:
        logger.info(f"  Only {len(json_files)} log files, no cleanup needed")
        return 0

    # Keep last N files
    files_to_delete = json_files[:-keep_last_n]

    logger.info(f"  Found {len(json_files)} log files")
    logger.info(f"  Keeping last {keep_last_n} files")
    logger.info(f"  Will delete {len(files_to_delete)} old log files")

    if dry_run:
        logger.info("  DRY RUN - no files will be deleted")
        return len(files_to_delete)

    deleted_count = 0
    for json_file in files_to_delete:
        try:
            json_file.unlink()
            deleted_count += 1
        except Exception as e:
            logger.warning(f"  Failed to delete {json_file.name}: {e}")

    logger.info(f"  ✅ Deleted {deleted_count} old log files")
    return deleted_count


def main():
    parser = argparse.ArgumentParser(
        description='Emergency Delta Lake transaction log cleanup',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        '--tables',
        nargs='*',
        help='Specific tables to clean (default: all tables)'
    )
    parser.add_argument(
        '--keep-logs',
        type=int,
        default=100,
        help='Number of recent log files to keep (default: 100)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be deleted without actually deleting'
    )
    parser.add_argument(
        '--skip-checkpoint',
        action='store_true',
        help='Skip checkpoint creation (only clean logs)'
    )

    args = parser.parse_args()

    logger.info("=" * 70)
    logger.info("Emergency Delta Lake Transaction Log Cleanup")
    logger.info("=" * 70)
    logger.warning("⚠️  WARNING: This will break time-travel queries!")
    logger.info(f"Keep last {args.keep_logs} log files per table")

    if args.dry_run:
        logger.info("DRY RUN MODE - No files will be deleted")

    try:
        # Initialize Delta Lake manager
        manager = get_delta_manager()

        # Get list of tables to clean
        if args.tables:
            tables_to_clean = args.tables
            logger.info(f"Cleaning specific tables: {', '.join(tables_to_clean)}")
        else:
            tables_to_clean = list(manager.tables.keys())
            logger.info("Cleaning all tables")

        total_deleted = 0
        checkpoint_count = 0

        for table_name in tables_to_clean:
            if table_name not in manager.tables:
                logger.warning(f"Unknown table: {table_name} - skipping")
                continue

            table_path = manager.tables[table_name]
            if not (table_path / "_delta_log").exists():
                logger.info(f"Table {table_name} has no data - skipping")
                continue

            logger.info(f"\nProcessing {table_name}...")

            # Step 1: Create checkpoint (unless skipped)
            if not args.skip_checkpoint:
                if force_checkpoint(table_path, table_name):
                    checkpoint_count += 1

            # Step 2: Clean up old logs
            deleted = cleanup_old_logs(table_path, table_name, args.keep_logs, args.dry_run)
            total_deleted += deleted

        logger.info("=" * 70)
        logger.info("✅ Cleanup complete!")
        logger.info(f"   Checkpoints created: {checkpoint_count}")
        logger.info(f"   Log files deleted: {total_deleted}")
        logger.info("=" * 70)

        if not args.dry_run:
            logger.warning("⚠️  Time-travel queries beyond the checkpoint are no longer possible!")
            logger.info("To verify tables are still accessible, run: python cli.py health")

        return 0

    except Exception as e:
        logger.error(f"Cleanup script failed: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
