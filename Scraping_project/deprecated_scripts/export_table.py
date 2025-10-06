#!/usr/bin/env python3
"""Delta Lake Export Utility - Export any Delta table to CSV/JSON/Parquet with all columns."""

import argparse
import os
from pathlib import Path

import duckdb

# Define base paths
DELTA_LAKE_BASE_PATH = Path(__file__).parent / "data" / "delta_lake"
EXPORT_PATH = Path(__file__).parent / "exports"


def export_table(table_name: str, format: str = "csv", output_file: str = None):
    """Export a Delta Lake table to various formats with ALL columns."""
    table_path = DELTA_LAKE_BASE_PATH / table_name

    if not table_path.exists():
        print(f"❌ Error: Table '{table_name}' not found.")
        list_available_tables()
        return False

    # Check for parquet files
    parquet_files = list(table_path.glob("*.parquet"))
    if not parquet_files:
        print(f"❌ Error: No parquet files found in '{table_name}'.")
        return False

    # Read ALL columns with schema merging
    sql_query = f"""
        SELECT *
        FROM read_parquet('{table_path}/*.parquet', union_by_name=True)
    """

    try:
        con = duckdb.connect(database=':memory:')
        print(f"🔎 Reading data from '{table_name}'...")

        result_df = con.execute(sql_query).fetchdf()

        if result_df.empty:
            print(f"⚠️  No data found in '{table_name}'.")
            return False

        # Prepare export
        os.makedirs(EXPORT_PATH, exist_ok=True)

        if output_file is None:
            output_file = EXPORT_PATH / f"{table_name}.{format}"
        else:
            output_file = Path(output_file)

        print(f"📊 Found {len(result_df)} rows with {len(result_df.columns)} columns")
        print(f"📋 Columns: {', '.join(result_df.columns.tolist())}")

        # Export based on format
        if format == "csv":
            result_df.to_csv(output_file, index=False)
        elif format == "json":
            result_df.to_json(output_file, orient='records', lines=True)
        elif format == "parquet":
            result_df.to_parquet(output_file, index=False)
        else:
            print(f"❌ Unsupported format: {format}")
            return False

        print(f"✅ Success! Data exported to: {output_file}")
        print(f"   Size: {output_file.stat().st_size / 1024 / 1024:.2f} MB")
        return True

    except Exception as e:
        print(f"🔥 An error occurred: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if 'con' in locals():
            con.close()


def export_all_tables(format: str = "csv"):
    """Export ALL Delta Lake tables."""
    if not DELTA_LAKE_BASE_PATH.exists():
        print(f"❌ Delta Lake path not found: {DELTA_LAKE_BASE_PATH}")
        return

    available_tables = [d.name for d in DELTA_LAKE_BASE_PATH.iterdir() if d.is_dir()]

    if not available_tables:
        print("⚠️  No tables found in Delta Lake")
        return

    print("=" * 80)
    print(f"EXPORTING ALL DELTA LAKE TABLES ({len(available_tables)} tables)")
    print("=" * 80)

    success_count = 0
    for table_name in sorted(available_tables):
        print(f"\n📦 Exporting '{table_name}'...")
        if export_table(table_name, format):
            success_count += 1

    print("\n" + "=" * 80)
    print(f"✅ Export complete: {success_count}/{len(available_tables)} tables exported")
    print("=" * 80)


def list_available_tables():
    """Lists the directories (tables) found in the Delta Lake path with statistics."""
    if not DELTA_LAKE_BASE_PATH.exists():
        print(f"❌ Delta Lake path not found: {DELTA_LAKE_BASE_PATH}")
        return

    available_tables = [d.name for d in DELTA_LAKE_BASE_PATH.iterdir() if d.is_dir()]

    if available_tables:
        print("\n📚 Available Delta Lake tables:")
        print("-" * 80)

        con = duckdb.connect(database=':memory:')

        for name in sorted(available_tables):
            table_path = DELTA_LAKE_BASE_PATH / name
            parquet_files = list(table_path.glob("*.parquet"))

            if parquet_files:
                try:
                    # Get row count
                    query = f"SELECT COUNT(*) as count FROM read_parquet('{table_path}/*.parquet', union_by_name=True)"
                    count = con.execute(query).fetchone()[0]
                    print(f"  ✓ {name:30} ({count:,} rows, {len(parquet_files)} parquet files)")
                except Exception as e:
                    print(f"  ✗ {name:30} (error: {e})")
            else:
                print(f"  ⚠ {name:30} (empty)")

        con.close()
        print("-" * 80)
    else:
        print("⚠️  No tables found in Delta Lake")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Export Delta Lake tables to CSV/JSON/Parquet with ALL columns",
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument(
        "table_name",
        type=str,
        nargs='?',
        help="Table name to export (e.g., 'stage1_discovery'). Use '--all' to export all tables."
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="Export ALL Delta Lake tables"
    )

    parser.add_argument(
        "--format",
        type=str,
        default="csv",
        choices=["csv", "json", "parquet"],
        help="Output format (default: csv)"
    )

    parser.add_argument(
        "--output",
        type=str,
        help="Output file path (optional)"
    )

    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available tables with statistics"
    )

    args = parser.parse_args()

    if args.list:
        list_available_tables()
    elif args.all:
        export_all_tables(args.format)
    elif args.table_name:
        export_table(args.table_name, args.format, args.output)
    else:
        parser.print_help()
        print("\n")
        list_available_tables()


if __name__ == "__main__":
    main()