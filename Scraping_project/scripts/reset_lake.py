#!/usr/bin/env python3
"""Reset the Delta Lake and re-seed it from CSV, guarded (#522, #576).

Default is a dry run: prints every table, its Delta version and a row estimate,
and changes nothing. Executing needs ``--execute --i-really-mean-it`` plus
``ALLOW_LAKE_RESET=1``; with ``ENV=production`` also ``--break-glass`` and a
typed confirmation. Every attempt is audited (see src/utils/destructive_guard).

The lake is the one the pipeline writes (``DELTA_LAKE_PATH``, else
``delta_lake.base_path``); this script used to flush ``data/delta_lake``
regardless, then re-seed wherever ``DELTA_LAKE_PATH`` pointed.

A full reset *moves* the lake to ``<lake>.bak-<UTC stamp>`` (instant, nothing
copied). To undo::

    mv data/delta_lake data/delta_lake.failed && mv data/delta_lake.bak-<stamp> data/delta_lake

``--no-backup`` deletes instead and additionally requires
``DELTA_ALLOW_HARD_DELETE=1`` (same rule as ``delete_table(hard=True)``).
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.destructive_guard import DestructiveOpRefused, add_guard_arguments, audit, authorize
from src.utils.lake_inventory import format_inventory, inventory, resolve_lake_path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

SEED_CSV = Path(__file__).parent.parent / "data" / "raw" / "uconn_urls.csv"


def flush_lake(lake: Path, *, backup: bool = True) -> Path | None:
    """Move the lake aside (default) or delete it. Returns the backup path."""
    if not lake.exists():
        logger.info("Delta Lake directory doesn't exist, nothing to flush")
        return None
    if not backup:
        if os.getenv("DELTA_ALLOW_HARD_DELETE") != "1":
            raise DestructiveOpRefused("--no-backup refused: set DELTA_ALLOW_HARD_DELETE=1 (irreversible)")
        logger.warning(f"Deleting (no backup): {lake}")
        shutil.rmtree(lake)
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = lake.with_name(f"{lake.name}.bak-{stamp}")
    shutil.move(str(lake), str(dest))
    logger.warning(f"Delta Lake moved to {dest} (restore: mv {dest} {lake})")
    return dest


def seed_lake(csv_path: Path = SEED_CSV) -> int:
    import pandas as pd

    from src.lakehouse.lakehouse_manager import get_delta_manager

    logger.info("🌱 Seeding Delta Lake...")
    if not csv_path.exists():
        raise FileNotFoundError(f"Seed file not found: {csv_path} (expected data/raw/uconn_urls.csv)")

    df = pd.read_csv(csv_path, header=None, names=["url"])
    logger.info(f"Loaded {len(df)} URLs from {csv_path}")
    df["url_hash"] = df["url"].apply(lambda url: hashlib.sha256(url.encode("utf-8")).hexdigest())
    df["added_at"] = pd.Timestamp.now().isoformat()

    manager = get_delta_manager()
    manager.write("seed_urls", df.to_dict("records"), mode="overwrite", async_write=False)
    try:
        logger.info(f"✅ seed_urls now contains {manager.count('seed_urls')} records")
    except Exception as e:
        logger.warning(f"⚠️  Could not verify record count: {e}")
    return len(df)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reset Delta Lake and re-seed from CSV (dry run unless --execute)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/reset_lake.py                       # dry run: what would be removed
  ALLOW_LAKE_RESET=1 python scripts/reset_lake.py --execute --i-really-mean-it
  ALLOW_LAKE_RESET=1 python scripts/reset_lake.py --seed-only --execute --i-really-mean-it
        """,
    )
    parser.add_argument("--seed-only", action="store_true", help="Only overwrite seed_urls (Delta history kept), do not move other tables")
    parser.add_argument("--no-backup", action="store_true", help="Delete instead of moving aside; needs DELTA_ALLOW_HARD_DELETE=1")
    parser.add_argument("--lake", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    add_guard_arguments(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.force:
        logger.error("--force was removed (#522): it skipped every confirmation. Use --execute --i-really-mean-it with ALLOW_LAKE_RESET=1.")
        return 2

    # The lake LakehouseManager writes (DELTA_LAKE_PATH / delta_lake.base_path), so
    # the flush and the re-seed hit the same directory.
    lake: Path = args.lake or resolve_lake_path()
    tables = inventory(lake)
    operation = "reseed-lake" if args.seed_only else "reset-lake"
    targets = ["seed_urls"] if args.seed_only else [t.name for t in tables] or [str(lake)]

    print("=" * 70)
    print(f"{operation}: {lake}")
    print("=" * 70)
    print(format_inventory([t for t in tables if t.name in targets] if args.seed_only else tables))
    if args.seed_only:
        print("\nWould OVERWRITE seed_urls from the seed CSV (earlier versions stay readable via time travel).")
    else:
        print(f"\nWould {'DELETE' if args.no_backup else 'move aside'} the whole lake, then re-seed seed_urls.")

    if args.execute and args.no_backup and not args.seed_only and os.getenv("DELTA_ALLOW_HARD_DELETE") != "1":
        audit(operation, targets, "refused", dry_run=False, details={"missing": ["DELTA_ALLOW_HARD_DELETE=1"]})
        logger.error("--no-backup refused: set DELTA_ALLOW_HARD_DELETE=1 (irreversible)")
        return 3

    try:
        if not authorize(operation, targets, execute=args.execute, i_really_mean_it=args.i_really_mean_it, break_glass=args.break_glass):
            print("\nDry run only; nothing changed. Re-run with --execute --i-really-mean-it and ALLOW_LAKE_RESET=1.")
            return 0
    except DestructiveOpRefused as e:
        logger.error(str(e))
        return 3

    details: dict[str, object] = {"tables": {t.name: {"version": t.version, "rows": t.rows} for t in tables}}
    try:
        if not args.seed_only:
            details["backup"] = str(flush_lake(lake, backup=not args.no_backup))
        details["seeded"] = seed_lake()
    except DestructiveOpRefused as e:
        audit(operation, targets, "refused", dry_run=False, details={**details, "error": str(e)})
        logger.error(str(e))
        return 3
    except Exception as e:
        audit(operation, targets, "failed", dry_run=False, details={**details, "error": repr(e)})
        logger.error(f"❌ {operation} failed: {e}", exc_info=True)
        return 1
    audit(operation, targets, "executed", dry_run=False, details=details)
    logger.info("🎉 Reset complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
