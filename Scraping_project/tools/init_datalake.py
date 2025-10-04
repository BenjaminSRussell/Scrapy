#!/usr/bin/env python3
"""
Initialize Delta Lake Data Warehouse

Creates all required Delta Lake tables with proper schemas, partitioning,
and indexes for the UConn Web Scraping Pipeline.

Usage:
    python tools/init_datalake.py
    python tools/init_datalake.py --drop-existing  # WARNING: Deletes all data!
"""

import argparse
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.datalake_schema import (
    DataLakeConfig,
    DeltaLakeSchema,
    TableType,
    get_create_table_sql,
    get_sample_queries
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_delta_table(table_path: Path, schema: dict, partition_cols: list = None):
    """Create a Delta Lake table with the specified schema"""
    try:
        from deltalake import write_deltalake
        import pyarrow as pa

        # Convert schema dict to PyArrow schema
        fields = []
        for col_name, col_type in schema.items():
            pa_type = {
                'string': pa.string(),
                'int': pa.int32(),
                'long': pa.int64(),
                'double': pa.float64(),
                'boolean': pa.bool_(),
                'timestamp': pa.timestamp('us')
            }.get(col_type, pa.string())

            fields.append(pa.field(col_name, pa_type))

        pa_schema = pa.schema(fields)

        # Create empty table with schema
        table_path.parent.mkdir(parents=True, exist_ok=True)

        # Write empty PyArrow table to create Delta structure
        import pandas as pd

        # Create DataFrame with correct dtypes from PyArrow schema
        empty_df = pd.DataFrame({col: pd.Series([], dtype='object') for col in schema.keys()})
        empty_table = pa.Table.from_pandas(empty_df, schema=pa_schema)

        write_deltalake(
            table_or_uri=str(table_path),
            data=empty_table,
            mode='overwrite',
            partition_by=partition_cols
        )

        logger.info(f"✅ Created Delta Lake table: {table_path}")
        if partition_cols:
            logger.info(f"   Partitioned by: {', '.join(partition_cols)}")

        return True

    except ImportError as e:
        logger.error(f"❌ Missing dependency: {e}")
        logger.error("   Install with: pip install deltalake pyarrow pandas")
        return False
    except Exception as e:
        logger.error(f"❌ Failed to create table {table_path}: {e}")
        return False


def initialize_warehouse(drop_existing: bool = False):
    """Initialize the complete Delta Lake warehouse"""

    logger.info("="*80)
    logger.info("INITIALIZING DELTA LAKE DATA WAREHOUSE")
    logger.info("="*80)

    config = DataLakeConfig()

    # Drop existing if requested
    if drop_existing:
        import shutil
        base_path = Path(config.BASE_PATH)
        if base_path.exists():
            logger.warning(f"⚠️  Dropping existing warehouse at {base_path}")
            shutil.rmtree(base_path)
            logger.info("✅ Existing warehouse removed")

    # Create all tables
    tables = [
        (
            Path(config.RAW_URLS_PATH),
            DeltaLakeSchema.RAW_URLS_SCHEMA,
            None,
            "Raw Discovered URLs"
        ),
        (
            Path(config.VALIDATED_URLS_PATH),
            DeltaLakeSchema.VALIDATED_URLS_SCHEMA,
            None,
            "Validated URLs with Status Codes"
        ),
        (
            Path(config.ENRICHED_CONTENT_PATH),
            DeltaLakeSchema.ENRICHED_CONTENT_SCHEMA,
            config.ENRICHED_PARTITION_COLS,
            "Enriched Content (Main Analytical Table)"
        ),
        (
            Path(config.LINK_GRAPH_PATH),
            DeltaLakeSchema.LINK_GRAPH_SCHEMA,
            None,
            "URL Link Graph Relationships"
        ),
        (
            Path(config.PERFORMANCE_METRICS_PATH),
            DeltaLakeSchema.PERFORMANCE_METRICS_SCHEMA,
            config.METRICS_PARTITION_COLS,
            "Performance Metrics Time Series"
        )
    ]

    success_count = 0
    for table_path, schema, partition_cols, description in tables:
        logger.info(f"\nCreating: {description}")
        logger.info(f"Path: {table_path}")
        logger.info(f"Columns: {len(schema)}")

        if create_delta_table(table_path, schema, partition_cols):
            success_count += 1

    logger.info("\n" + "="*80)
    logger.info(f"WAREHOUSE INITIALIZATION COMPLETE: {success_count}/{len(tables)} tables created")
    logger.info("="*80)

    # Generate DuckDB setup script
    generate_duckdb_setup()

    # Print sample queries
    print_sample_queries()

    return success_count == len(tables)


def generate_duckdb_setup():
    """Generate SQL script for DuckDB access"""

    config = DataLakeConfig()
    setup_file = Path("data/datalake/setup_duckdb.sql")
    setup_file.parent.mkdir(parents=True, exist_ok=True)

    sql_lines = [
        "-- DuckDB Setup Script for UConn Pipeline Data Lake",
        "-- Generated automatically by init_datalake.py",
        "",
        "-- Load Delta Lake extension",
        "INSTALL delta;",
        "LOAD delta;",
        "",
        "-- Create views for each table",
        ""
    ]

    table_paths = {
        'raw_urls': config.RAW_URLS_PATH,
        'validated_urls': config.VALIDATED_URLS_PATH,
        'enriched_content': config.ENRICHED_CONTENT_PATH,
        'link_graph': config.LINK_GRAPH_PATH,
        'performance_metrics': config.PERFORMANCE_METRICS_PATH
    }

    for table_name, table_path in table_paths.items():
        sql_lines.append(f"CREATE OR REPLACE VIEW {table_name} AS")
        sql_lines.append(f"  SELECT * FROM delta_scan('{table_path}');")
        sql_lines.append("")

    setup_file.write_text('\n'.join(sql_lines))
    logger.info(f"\n✅ DuckDB setup script created: {setup_file}")
    logger.info(f"   Usage: duckdb < {setup_file}")


def print_sample_queries():
    """Print sample analytical queries"""

    print("\n" + "="*80)
    print("SAMPLE ANALYTICAL QUERIES")
    print("="*80)

    queries = get_sample_queries()

    print("\nTo run these queries:")
    print("1. Install DuckDB: pip install duckdb")
    print("2. Run: python -c \"import duckdb; duckdb.sql('LOAD delta; <your_query>').show()\"")
    print("\nOr use the DuckDB CLI:")
    print("  duckdb")
    print("  > LOAD delta;")
    print("  > <paste query>;")

    for query_name, query_sql in queries.items():
        print(f"\n## {query_name.replace('_', ' ').title()}")
        print(f"```sql{query_sql}```")


def main():
    parser = argparse.ArgumentParser(
        description="Initialize Delta Lake data warehouse for UConn pipeline"
    )
    parser.add_argument(
        '--drop-existing',
        action='store_true',
        help='Drop existing warehouse (WARNING: deletes all data!)'
    )

    args = parser.parse_args()

    if args.drop_existing:
        confirm = input("⚠️  This will DELETE ALL DATA in the warehouse. Continue? (yes/no): ")
        if confirm.lower() != 'yes':
            logger.info("Aborted.")
            return 1

    success = initialize_warehouse(drop_existing=args.drop_existing)

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
