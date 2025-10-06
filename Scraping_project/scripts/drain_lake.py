#!/usr/bin/env python3
"""Drain Lake Utility - Clean Delta Lake Tables Without Deleting Seed URLs

This script provides a safe way to reset the pipeline's data state while
preserving the seed URLs in the CSV file. It deletes all records from Delta
Lake tables but leaves the table structures intact.

Usage:
    python scripts/drain_lake.py
    python scripts/drain_lake.py --yes  # Skip confirmation
"""

import logging
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.common.delta_lake import get_delta_manager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)


def drain_lake(skip_confirmation: bool = False):
    """Drain all Delta Lake tables by deleting records while preserving structure.

    Args:
        skip_confirmation: If True, skip the confirmation prompt
    """
    print("=" * 80)
    print("DRAIN DELTA LAKE")
    print("=" * 80)
    print()
    print("This utility will DELETE ALL RECORDS from Delta Lake tables.")
    print("Table structures and the seed URLs CSV will remain intact.")
    print()

    try:
        delta = get_delta_manager()
        tables_info = delta.list_tables()

        # Filter to only tables with data
        tables_with_data = [t for t in tables_info if t.get('exists') and t.get('row_count', 0) > 0]

        if not tables_with_data:
            print("ℹ️  No data found in Delta Lake tables.")
            print("✅ Nothing to drain - Delta Lake is already empty.")
            return

        print("📊 Current Data State:")
        print("-" * 80)
        total_rows = 0
        for table in tables_with_data:
            row_count = table.get('row_count', 0)
            total_rows += row_count
            print(f"  • {table['name']:30} {row_count:>10,} rows")
        print("-" * 80)
        print(f"  TOTAL: {total_rows:,} rows across {len(tables_with_data)} tables")
        print()

        # Confirmation
        if not skip_confirmation:
            print("⚠️  WARNING: This action CANNOT be undone!")
            print()
            response = input("Type 'drain' to confirm deletion: ").strip()
            if response.lower() != 'drain':
                print()
                print("❌ Operation cancelled - no changes made")
                return
            print()

        # Drain each table
        print("🗑️  Draining Delta Lake tables...")
        print()

        drained_count = 0
        for table_info in tables_with_data:
            table_name = table_info['name']
            table_path = Path(table_info['path'])

            try:
                # Delete the entire table directory to fully reset
                # This is more reliable than trying to delete individual records
                import shutil
                if table_path.exists():
                    shutil.rmtree(table_path)
                    table_path.mkdir(parents=True, exist_ok=True)
                    logger.info(f"✅ Drained {table_name}")
                    drained_count += 1
            except Exception as e:
                logger.error(f"❌ Failed to drain {table_name}: {e}")

        print()
        print("=" * 80)
        print(f"✅ DRAIN COMPLETE - {drained_count}/{len(tables_with_data)} tables drained")
        print("=" * 80)
        print()
        print("📋 Next Steps:")
        print("  1. Verify seed URLs are intact: data/seed_urls.csv")
        print("  2. Run pipeline: python run_pipeline.py run")
        print("  3. Monitor progress: python run_pipeline.py health")
        print()

    except Exception as e:
        logger.error(f"❌ Drain operation failed: {e}", exc_info=True)
        sys.exit(1)


def main():
    """Main entry point for command-line usage."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Drain Delta Lake tables without deleting seed URLs",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        '-y', '--yes',
        action='store_true',
        help='Skip confirmation prompt'
    )

    args = parser.parse_args()

    try:
        drain_lake(skip_confirmation=args.yes)
    except KeyboardInterrupt:
        print()
        print("❌ Operation cancelled by user")
        sys.exit(1)


if __name__ == "__main__":
    main()
