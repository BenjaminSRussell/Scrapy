#!/usr/bin/env python3
"""Standalone Delta Lake Reset Script

This script provides a powerful tool for managing your Delta Lake data environment.
It can completely wipe all tables and re-seed from the CSV seed file.

Usage:
    python scripts/reset_lake.py              # Interactive mode with confirmation
    python scripts/reset_lake.py --force      # Skip confirmation
    python scripts/reset_lake.py --seed-only  # Only re-seed, don't wipe
"""

import argparse
import hashlib
import logging
import shutil
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from src.common.constants import DELTA_LAKE
from src.common.delta_lake import get_delta_manager

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def flush_lake():
    """Delete all Delta Lake table directories."""
    logger.info("🔥 Flushing Delta Lake...")

    if not DELTA_LAKE.exists():
        logger.info("Delta Lake directory doesn't exist, nothing to flush")
        return

    logger.info(f"Deleting: {DELTA_LAKE}")
    shutil.rmtree(DELTA_LAKE)
    logger.info("✅ Delta Lake flushed successfully")


def seed_lake():
    """Seed Delta Lake with URLs from CSV."""
    logger.info("🌱 Seeding Delta Lake...")

    # Find the CSV file
    csv_path = Path(__file__).parent.parent / "data" / "raw" / "uconn_urls.csv"

    if not csv_path.exists():
        logger.error(f"❌ Seed file not found: {csv_path}")
        logger.error("Please ensure uconn_urls.csv exists in data/raw/")
        sys.exit(1)

    logger.info(f"Loading seed URLs from: {csv_path}")

    # Read CSV
    try:
        df = pd.read_csv(csv_path, header=None, names=["url"])
        logger.info(f"Loaded {len(df)} URLs from CSV")
    except Exception as e:
        logger.error(f"❌ Failed to read CSV: {e}")
        sys.exit(1)

    # Validate CSV has required columns
    if "url" not in df.columns:
        logger.error("❌ CSV must have a 'url' column")
        sys.exit(1)

    # Add url_hash column
    logger.info("Calculating URL hashes...")
    df["url_hash"] = df["url"].apply(lambda url: hashlib.sha256(url.encode("utf-8")).hexdigest())

    # Add timestamp
    df["added_at"] = pd.Timestamp.now().isoformat()

    # Convert to records
    seed_records = df.to_dict("records")

    # Get Delta Lake manager
    logger.info("Initializing Delta Lake manager...")
    manager = get_delta_manager()

    # Write to seed_urls table
    logger.info(f"Writing {len(seed_records)} records to seed_urls table...")
    try:
        manager.write("seed_urls", seed_records, mode="overwrite", async_write=False)
        logger.info("✅ Seed URLs written successfully")
    except Exception as e:
        logger.error(f"❌ Failed to write seed URLs: {e}")
        sys.exit(1)

    # Verify
    try:
        count = manager.count("seed_urls")
        logger.info(f"✅ Verified: seed_urls table contains {count} records")
    except Exception as e:
        logger.warning(f"⚠️  Could not verify record count: {e}")


def main():
    """Main execution."""
    parser = argparse.ArgumentParser(
        description="Reset Delta Lake and re-seed from CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/reset_lake.py              # Interactive reset with confirmation
  python scripts/reset_lake.py --force      # Skip confirmation prompt
  python scripts/reset_lake.py --seed-only  # Only re-seed, don't wipe tables
        """,
    )

    parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Only re-seed seed_urls table, do not wipe other tables",
    )

    args = parser.parse_args()

    logger.info("=" * 70)
    logger.info("Delta Lake Reset Script")
    logger.info("=" * 70)

    # Confirmation prompt
    if not args.force:
        if args.seed_only:
            logger.warning("⚠️  This will OVERWRITE the seed_urls table")
        else:
            logger.warning("⚠️  This will DELETE ALL Delta Lake tables and re-seed!")

        confirmation = input("\nType 'yes' to continue: ")
        if confirmation.lower() != "yes":
            logger.info("❌ Operation cancelled")
            sys.exit(0)

    # Execute operations
    try:
        if not args.seed_only:
            flush_lake()

        seed_lake()

        logger.info("=" * 70)
        logger.info("🎉 Reset complete!")
        logger.info("=" * 70)

    except KeyboardInterrupt:
        logger.warning("\n⚠️  Operation interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
