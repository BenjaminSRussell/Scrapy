#!/usr/bin/env python3
"""Wipe the Delta lake and re-seed seed_urls from data/raw/uconn_urls.csv.

Destructive: guarded by src/utils/destructive_guard.py (#522, #573, #576).
"""

import argparse
import hashlib
import logging
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from src.core.constants import DELTA_LAKE
from src.lakehouse.lakehouse_manager import get_delta_manager
from src.utils.destructive_guard import (
    add_backup_argument,
    add_guard_arguments,
    authorize,
    guard_flags,
    guarded_lake_wipe,
    lake_targets,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

def flush_lake():
    logger.info("🔥 Flushing Delta Lake...")

    if not DELTA_LAKE.exists():
        logger.info("Delta Lake directory doesn't exist, nothing to flush")
        return

    logger.info(f"Deleting: {DELTA_LAKE}")
    shutil.rmtree(DELTA_LAKE)
    logger.info("✅ Delta Lake flushed successfully")

def seed_lake():
    logger.info("🌱 Seeding Delta Lake...")

    csv_path = Path(__file__).parent.parent / "data" / "raw" / "uconn_urls.csv"

    if not csv_path.exists():
        logger.error(f"❌ Seed file not found: {csv_path}")
        logger.error("Please ensure uconn_urls.csv exists in data/raw/")
        sys.exit(1)

    logger.info(f"Loading seed URLs from: {csv_path}")

    try:
        df = pd.read_csv(csv_path, header=None, names=["url"])
        logger.info(f"Loaded {len(df)} URLs from CSV")
    except Exception as e:
        logger.error(f"❌ Failed to read CSV: {e}")
        sys.exit(1)

    if "url" not in df.columns:
        logger.error("❌ CSV must have a 'url' column")
        sys.exit(1)

    logger.info("Calculating URL hashes...")
    df["url_hash"] = df["url"].apply(lambda url: hashlib.sha256(url.encode("utf-8")).hexdigest())

    df["added_at"] = pd.Timestamp.now().isoformat()

    seed_records = df.to_dict("records")

    logger.info("Initializing Delta Lake manager...")
    manager = get_delta_manager()

    logger.info(f"Writing {len(seed_records)} records to seed_urls table...")
    try:
        manager.write("seed_urls", seed_records, mode="overwrite", async_write=False)
        logger.info("✅ Seed URLs written successfully")
    except Exception as e:
        logger.error(f"❌ Failed to write seed URLs: {e}")
        sys.exit(1)

    try:
        count = manager.count("seed_urls")
        logger.info(f"✅ Verified: seed_urls table contains {count} records")
    except Exception as e:
        logger.warning(f"⚠️  Could not verify record count: {e}")

def main():
    parser = argparse.ArgumentParser(
        description="Reset Delta Lake and re-seed from CSV (dry-run unless --confirm; see scripts/README.md)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/reset_lake.py                       # dry-run: list tables + row estimates, exit 2
  python scripts/reset_lake.py --confirm             # typed confirmation, then wipe + re-seed
  python scripts/reset_lake.py --confirm --backup-dir /backups
  ALLOW_LAKE_RESET=1 python scripts/reset_lake.py --confirm --yes   # automation
  python scripts/reset_lake.py --seed-only --confirm # overwrite seed_urls only
ENV=production additionally needs --i-know-what-im-doing, ALLOW_LAKE_RESET=1 and typing 'production'.
        """,
    )

    parser.add_argument("--force", action="store_true", help="Deprecated: same as --confirm --yes")
    add_guard_arguments(parser)
    add_backup_argument(parser)

    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Only re-seed seed_urls table, do not wipe other tables",
    )

    args = parser.parse_args()

    logger.info("=" * 70)
    logger.info("Delta Lake Reset Script")
    logger.info("=" * 70)

    try:
        if args.seed_only:
            targets = [t for t in lake_targets(DELTA_LAKE) if Path(t["table"]).name == "seed_urls"]
            decision = authorize("overwrite the seed_urls table", targets=targets, **guard_flags(args))
        else:
            decision = guarded_lake_wipe(DELTA_LAKE, args, action="wipe the Delta lake and re-seed")
        if not decision.proceed:
            sys.exit(decision.exit_code)

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
