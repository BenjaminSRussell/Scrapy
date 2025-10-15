#!/usr/bin/env python3
"""
Custom Reseed Script for Delta Lake
Loads uconn_urls.csv into Delta Lake seed_urls table and validates the setup.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import shutil
import sys
from pathlib import Path

import pandas as pd

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.common.constants import DELTA_LAKE
from src.common.delta_lake import get_delta_manager

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def clear_delta_lake():
    """Delete all Delta Lake tables."""
    if DELTA_LAKE.exists():
        logger.info(f"Deleting Delta Lake directory: {DELTA_LAKE}")
        shutil.rmtree(DELTA_LAKE)
        logger.info("✅ Delta Lake wiped")
    else:
        logger.info("Delta Lake directory does not exist, nothing to clear")


def load_seed_urls(csv_path: Path, manager) -> int:
    """Load seed URLs from CSV into Delta Lake.

    Args:
        csv_path: Path to uconn_urls.csv
        manager: DeltaLakeManager instance

    Returns:
        Number of URLs loaded
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"Seed file not found: {csv_path}")

    logger.info(f"Loading seed URLs from: {csv_path}")

    # Read CSV (no header, just URLs)
    df = pd.read_csv(csv_path, header=None, names=["url"])

    # Remove duplicates and empty URLs
    original_count = len(df)
    df = df.dropna(subset=["url"])
    df = df[df["url"].str.strip() != ""]
    df = df.drop_duplicates(subset=["url"])

    logger.info(
        f"Loaded {original_count} URLs, {len(df)} unique URLs after deduplication"
    )

    # Add url_hash column (SHA256 hash of URL)
    df["url_hash"] = df["url"].apply(
        lambda url: hashlib.sha256(url.encode("utf-8")).hexdigest()
    )

    # Add timestamp
    df["added_at"] = pd.Timestamp.now().isoformat()

    # Convert to list of dictionaries
    seed_records = df.to_dict("records")

    logger.info(f"Seeding {len(seed_records)} URLs into Delta Lake...")

    # Write to Delta Lake (synchronous write to ensure completion)
    manager.write("seed_urls", seed_records, mode="overwrite", async_write=False)

    logger.info(f"✅ Seed URLs loaded: {len(seed_records)} records")

    return len(seed_records)


def validate_seed_urls(manager) -> dict:
    """Validate that seed URLs were loaded correctly.

    Args:
        manager: DeltaLakeManager instance

    Returns:
        Dictionary with validation results
    """
    logger.info("Validating seed URLs table...")

    try:
        # Check if table exists
        count = manager.count("seed_urls")

        if count == 0:
            logger.error("❌ seed_urls table exists but has 0 rows!")
            return {"valid": False, "count": 0, "error": "Empty table"}

        # Read sample records
        sample = manager.read("seed_urls", columns=["url", "url_hash", "added_at"])[:5]

        logger.info(f"✅ seed_urls table validated: {count} records")
        logger.info("Sample records:")
        for i, record in enumerate(sample[:3], 1):
            logger.info(f"  {i}. {record.get('url', 'N/A')}")

        return {"valid": True, "count": count, "sample": sample}

    except Exception as e:
        logger.error(f"❌ Validation failed: {e}")
        return {"valid": False, "count": 0, "error": str(e)}


def check_table_stats(manager):
    """Check statistics for all Delta Lake tables."""
    logger.info("\n" + "=" * 60)
    logger.info("Delta Lake Table Statistics")
    logger.info("=" * 60)

    tables = manager.list_tables()

    for table in tables:
        status = "✓" if table["exists"] else "✗"
        logger.info(
            f"{status} {table['name']}: {table['row_count']} rows, {table['parquet_files']} parquet files"
        )

        if "error" in table:
            logger.warning(f"  ⚠️  Error: {table['error']}")

    logger.info("=" * 60 + "\n")


def main():
    """Main entry point for reseed script."""
    parser = argparse.ArgumentParser(
        description="Reseed Delta Lake with uconn_urls.csv",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--csv", default="data/raw/uconn_urls.csv", help="Path to uconn_urls.csv file"
    )

    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear all Delta Lake tables before reseeding (WARNING: destructive!)",
    )

    parser.add_argument(
        "--no-validate", action="store_true", help="Skip validation after seeding"
    )

    parser.add_argument(
        "--force", action="store_true", help="Skip confirmation prompts"
    )

    args = parser.parse_args()

    # Resolve CSV path
    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = Path(__file__).parent / csv_path

    logger.info("🚀 Delta Lake Reseed Script Starting...")
    logger.info(f"CSV file: {csv_path}")

    try:
        # Check if CSV exists
        if not csv_path.exists():
            logger.error(f"❌ CSV file not found: {csv_path}")
            sys.exit(1)

        # Clear Delta Lake if requested
        if args.clear:
            logger.warning("⚠️  This will DELETE all Delta Lake tables!")
            if not args.force:
                confirmation = input("Are you sure? Type 'yes' to continue: ")
                if confirmation.lower() != "yes":
                    logger.info("Operation cancelled.")
                    sys.exit(0)

            clear_delta_lake()

        # Create Delta Lake manager
        logger.info("Initializing Delta Lake manager...")
        manager = get_delta_manager()
        logger.info("✅ Delta Lake manager initialized")

        # Load seed URLs
        url_count = load_seed_urls(csv_path, manager)

        # Validate if not skipped
        if not args.no_validate:
            validation = validate_seed_urls(manager)

            if not validation["valid"]:
                logger.error(
                    f"❌ Validation failed: {validation.get('error', 'Unknown error')}"
                )
                sys.exit(1)

            # Check all table statistics
            check_table_stats(manager)

        logger.info("🎉 Reseed Complete!")
        logger.info(f"✅ Loaded {url_count} seed URLs")
        logger.info("\nNext steps:")
        logger.info("  1. Start the pipeline: python start.py --env local")
        logger.info("  2. Monitor progress: python cli.py health")
        logger.info("  3. View Grafana dashboard: http://localhost:3000")

        sys.exit(0)

    except FileNotFoundError as e:
        logger.error(f"❌ File not found: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
