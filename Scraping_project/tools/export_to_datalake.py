#!/usr/bin/env python3
"""
Export Pipeline Data to Delta Lake

This script converts the final enriched_content.jsonl output to Parquet format
and writes it into a Delta Lake table for efficient querying with DuckDB/SQL.

Usage:
    python tools/export_to_datalake.py
    python tools/export_to_datalake.py --input data/processed/stage03/enriched_content.jsonl
    python tools/export_to_datalake.py --output data/datalake/enriched_content
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_jsonl(input_file: Path) -> pd.DataFrame:
    """Load JSONL file into pandas DataFrame"""
    logger.info(f"Loading data from {input_file}")

    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    records = []
    with open(input_file, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            try:
                record = json.loads(line)
                records.append(record)
            except json.JSONDecodeError as e:
                logger.warning(f"Skipping malformed JSON on line {line_num}: {e}")
                continue

    if not records:
        raise ValueError(f"No valid records found in {input_file}")

    df = pd.DataFrame(records)
    logger.info(f"Loaded {len(df):,} records with {len(df.columns)} columns")

    return df


def write_to_delta(df: pd.DataFrame, output_path: Path, mode: str = "overwrite"):
    """Write DataFrame to Delta Lake table"""
    try:
        from deltalake import write_deltalake

        logger.info(f"Writing {len(df):,} records to Delta Lake: {output_path}")

        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Write to Delta Lake
        write_deltalake(
            table_or_uri=str(output_path),
            data=df,
            mode=mode,  # "overwrite", "append", "error", "ignore"
            schema_mode="overwrite",  # Allow schema evolution
            engine="pyarrow"
        )

        logger.info(f"✅ Successfully wrote Delta Lake table to {output_path}")
        logger.info(f"   Columns: {', '.join(df.columns.tolist())}")
        logger.info(f"   Rows: {len(df):,}")

    except ImportError:
        logger.error("deltalake package not installed. Run: pip install deltalake")
        sys.exit(1)


def write_to_parquet(df: pd.DataFrame, output_path: Path):
    """Write DataFrame to Parquet file (fallback if Delta Lake not available)"""
    logger.info(f"Writing {len(df):,} records to Parquet: {output_path}")

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to Parquet
    df.to_parquet(
        output_path,
        engine='pyarrow',
        compression='snappy',
        index=False
    )

    logger.info(f"✅ Successfully wrote Parquet file to {output_path}")


def show_query_examples(datalake_path: Path):
    """Show example queries using DuckDB"""
    print("\n" + "="*80)
    print("DATA LAKE QUERY EXAMPLES")
    print("="*80)

    print("\n1. Query using DuckDB in Python:")
    print("```python")
    print("import duckdb")
    print(f"datalake_path = '{datalake_path}'")
    print("")
    print("# Connect to DuckDB (in-memory)")
    print("con = duckdb.connect()")
    print("")
    print("# Query Delta Lake table")
    print("df = con.execute('''")
    print("    SELECT url, title, content_preview, extracted_at")
    print("    FROM delta_scan('{datalake_path}')")
    print("    WHERE title IS NOT NULL")
    print("    ORDER BY extracted_at DESC")
    print("    LIMIT 10")
    print("''').df()")
    print("")
    print("print(df)")
    print("```")

    print("\n2. Query using DuckDB CLI:")
    print("```bash")
    print("duckdb")
    print("")
    print("# In DuckDB shell:")
    print(f"SELECT COUNT(*) FROM delta_scan('{datalake_path}');")
    print("")
    print("# Get URLs by department")
    print(f"SELECT url, title")
    print(f"FROM delta_scan('{datalake_path}')")
    print("WHERE url LIKE '%/academics/%'")
    print("LIMIT 5;")
    print("```")

    print("\n3. Export to CSV:")
    print("```python")
    print("import duckdb")
    print("")
    print("con = duckdb.connect()")
    print("con.execute('''")
    print(f"    COPY (SELECT * FROM delta_scan('{datalake_path}'))")
    print("    TO 'output.csv' (HEADER, DELIMITER ',');")
    print("''')")
    print("```")

    print("\n" + "="*80)


def main():
    parser = argparse.ArgumentParser(
        description="Export pipeline data to Delta Lake for analytics"
    )
    parser.add_argument(
        '--input',
        type=Path,
        default=Path('data/processed/stage03/enriched_content.jsonl'),
        help='Input JSONL file (default: data/processed/stage03/enriched_content.jsonl)'
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=Path('data/datalake/enriched_content'),
        help='Output Delta Lake path (default: data/datalake/enriched_content)'
    )
    parser.add_argument(
        '--mode',
        choices=['overwrite', 'append', 'error', 'ignore'],
        default='overwrite',
        help='Write mode for Delta Lake (default: overwrite)'
    )
    parser.add_argument(
        '--parquet-only',
        action='store_true',
        help='Write Parquet file instead of Delta Lake'
    )

    args = parser.parse_args()

    try:
        # Load data
        df = load_jsonl(args.input)

        # Write to Delta Lake or Parquet
        if args.parquet_only:
            parquet_path = args.output.with_suffix('.parquet')
            write_to_parquet(df, parquet_path)
            print(f"\n✅ Parquet file created: {parquet_path}")
        else:
            write_to_delta(df, args.output, mode=args.mode)
            print(f"\n✅ Delta Lake table created: {args.output}")
            show_query_examples(args.output)

        return 0

    except Exception as e:
        logger.error(f"Export failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
