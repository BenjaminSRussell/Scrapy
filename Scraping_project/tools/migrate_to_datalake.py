#!/usr/bin/env python3
"""
Migrate Legacy Data to Delta Lake

This script helps migrate existing data from the legacy file-based storage
to the new Delta Lake format. It preserves all data and ensures a smooth
transition to the new storage system.

Usage:
    python tools/migrate_to_datalake.py
    python tools/migrate_to_datalake.py --skip-validation
    python tools/migrate_to_datalake.py --dry-run
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd
from deltalake import write_deltalake

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Source and target path mappings
MIGRATION_PATHS = {
    'stage1': {
        'source': Path('data/processed/stage01/discovery_output.jsonl'),
        'target': Path('data/datalake/raw_urls')
    },
    'stage2': {
        'source': Path('data/processed/stage02/validated_urls.jsonl'),
        'target': Path('data/datalake/validated_urls')
    },
    'stage3': {
        'source': Path('data/processed/stage03/enriched_content.jsonl'),
        'target': Path('data/datalake/enriched_content')
    },
}


def load_jsonl(file_path: Path) -> pd.DataFrame:
    """Load JSONL file into pandas DataFrame"""
    logger.info(f"Loading data from {file_path}")

    if not file_path.exists():
        logger.warning(f"File not found: {file_path}")
        return None

    try:
        records = []
        with open(file_path, encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    record = json.loads(line)
                    records.append(record)
                except json.JSONDecodeError as e:
                    logger.warning(f"Skipping malformed JSON on line {line_num}: {e}")
                    continue

        if not records:
            logger.warning(f"No valid records found in {file_path}")
            return None

        df = pd.DataFrame(records)
        logger.info(f"Loaded {len(df):,} records with {len(df.columns)} columns")
        return df

    except Exception as e:
        logger.error(f"Error loading {file_path}: {e}")
        return None


def write_to_delta(df: pd.DataFrame, output_path: Path, dry_run: bool = False):
    """Write DataFrame to Delta Lake table"""
    if dry_run:
        logger.info(f"[DRY RUN] Would write {len(df):,} records to {output_path}")
        return True

    try:
        logger.info(f"Writing {len(df):,} records to Delta Lake: {output_path}")

        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Write to Delta Lake
        write_deltalake(
            table_or_uri=str(output_path),
            data=df,
            mode='overwrite',  # Replace existing data
            schema_mode='overwrite',  # Allow schema evolution
            engine='pyarrow'
        )

        logger.info(f"✅ Successfully wrote Delta Lake table to {output_path}")
        logger.info(f"   Columns: {', '.join(df.columns.tolist())}")
        logger.info(f"   Rows: {len(df):,}")
        return True

    except Exception as e:
        logger.error(f"❌ Error writing to Delta Lake: {e}")
        return False


def validate_migration(source_df: pd.DataFrame, target_path: Path) -> bool:
    """Verify migrated data matches source"""
    try:
        import duckdb
        con = duckdb.connect()

        # Read from Delta Lake
        target_df = con.execute(f"SELECT * FROM delta_scan('{target_path}')").df()

        # Compare row counts
        if len(source_df) != len(target_df):
            logger.error(f"❌ Row count mismatch: Source={len(source_df)}, Target={len(target_df)}")
            return False

        # Compare column counts
        if len(source_df.columns) != len(target_df.columns):
            logger.error("❌ Column count mismatch!")
            logger.error(f"Source columns: {source_df.columns.tolist()}")
            logger.error(f"Target columns: {target_df.columns.tolist()}")
            return False

        # Verify content (row sample)
        sample_size = min(1000, len(source_df))
        sample_idx = source_df.sample(n=sample_size, random_state=42).index

        for idx in sample_idx:
            source_row = source_df.iloc[idx].to_dict()
            target_row = target_df.iloc[idx].to_dict()

            if source_row != target_row:
                logger.error(f"❌ Data mismatch at row {idx}!")
                return False

        logger.info(f"✅ Validated {sample_size:,} random rows - Data matches!")
        return True

    except Exception as e:
        logger.error(f"❌ Validation error: {e}")
        return False


def migrate_stage(stage: str, paths: dict, dry_run: bool = False, skip_validation: bool = False) -> bool:
    """Migrate data for a single pipeline stage"""
    logger.info(f"\n{'='*60}")
    logger.info(f"Migrating {stage.upper()} data")
    logger.info(f"{'='*60}")

    source_path = paths['source']
    target_path = paths['target']

    # Load source data
    df = load_jsonl(source_path)
    if df is None:
        return False

    # Write to Delta Lake
    success = write_to_delta(df, target_path, dry_run)
    if not success or dry_run:
        return False

    # Validate migration
    if not skip_validation:
        if not validate_migration(df, target_path):
            logger.error("❌ Validation failed! Data may be corrupted.")
            return False

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Migrate legacy data to Delta Lake format"
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be migrated without making changes'
    )
    parser.add_argument(
        '--skip-validation',
        action='store_true',
        help='Skip post-migration validation'
    )
    parser.add_argument(
        '--stages',
        nargs='+',
        choices=['stage1', 'stage2', 'stage3'],
        default=['stage1', 'stage2', 'stage3'],
        help='Specific stages to migrate (default: all)'
    )

    args = parser.parse_args()

    try:
        if args.dry_run:
            logger.info("🔍 DRY RUN - No changes will be made")

        success = True
        for stage in args.stages:
            stage_success = migrate_stage(
                stage,
                MIGRATION_PATHS[stage],
                dry_run=args.dry_run,
                skip_validation=args.skip_validation
            )
            success = success and stage_success

        if success:
            if args.dry_run:
                logger.info("\n✅ Dry run completed successfully")
            else:
                logger.info("\n✅ Migration completed successfully")
                logger.info("\nQuery examples:")
                logger.info("  duckdb -c \"SELECT COUNT(*) FROM delta_scan('data/datalake/raw_urls')\"")
                logger.info("  duckdb -c \"SELECT COUNT(*) FROM delta_scan('data/datalake/validated_urls')\"")
                logger.info("  duckdb -c \"SELECT COUNT(*) FROM delta_scan('data/datalake/enriched_content')\"")
            return 0
        else:
            logger.error("\n❌ Migration failed!")
            return 1

    except KeyboardInterrupt:
        logger.info("\n⚠️ Migration interrupted by user")
        return 1
    except Exception as e:
        logger.error(f"\n❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())