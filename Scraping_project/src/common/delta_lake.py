"""
Delta Lake integration for pipeline storage.

Provides ACID transactions, time travel, schema evolution for all pipeline data.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from deltalake import DeltaTable, write_deltalake
    import pyarrow as pa
    import pyarrow.parquet as pq
    DELTA_AVAILABLE = True
except ImportError:
    DELTA_AVAILABLE = False
    DeltaTable = None
    write_deltalake = None
    pa = None

from src.common.constants import (
    DELTA_RAW_URLS,
    DELTA_VALIDATED_URLS,
    DELTA_ENRICHED_CONTENT,
    DELTA_LINK_GRAPH,
    DELTA_PERFORMANCE_METRICS
)

logger = logging.getLogger(__name__)


class DeltaLakeWriter:
    """Write data to Delta Lake tables with ACID guarantees."""

    def __init__(self, table_path: Path, partition_by: list[str] | None = None):
        """
        Initialize Delta Lake writer.

        Args:
            table_path: Path to Delta Lake table
            partition_by: Columns to partition by (e.g., ['year', 'month', 'day'])
        """
        if not DELTA_AVAILABLE:
            raise ImportError(
                "Delta Lake not available. Install with: pip install deltalake pyarrow"
            )

        self.table_path = str(table_path)
        self.partition_by = partition_by or []
        self.table_path_obj = Path(table_path)

        # Ensure directory exists
        self.table_path_obj.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        data: list[dict[str, Any]],
        mode: str = "append",
        schema: pa.Schema | None = None
    ):
        """
        Write data to Delta Lake table.

        Args:
            data: List of dictionaries to write
            mode: Write mode ('append', 'overwrite', 'merge')
            schema: PyArrow schema (auto-inferred if None)
        """
        if not data:
            logger.warning(f"No data to write to {self.table_path}")
            return

        # Add ingestion timestamp
        for record in data:
            if '_ingestion_time' not in record:
                record['_ingestion_time'] = datetime.now().isoformat()

        # Convert to PyArrow Table
        if schema:
            table = pa.Table.from_pylist(data, schema=schema)
        else:
            table = pa.Table.from_pylist(data)

        # Write to Delta Lake
        write_deltalake(
            self.table_path,
            table,
            mode=mode,
            partition_by=self.partition_by if self.partition_by else None,
            schema_mode="merge" if mode == "append" else "overwrite"
        )

        logger.info(f"Wrote {len(data)} records to {self.table_path} (mode={mode})")

    def merge(
        self,
        data: list[dict[str, Any]],
        predicate: str,
        match_update: dict[str, str] | None = None,
        not_match_insert: dict[str, str] | None = None
    ):
        """
        Merge (upsert) data into Delta Lake table.

        Args:
            data: New data to merge
            predicate: Merge condition (e.g., "target.url = source.url")
            match_update: Column mappings for matched rows
            not_match_insert: Column mappings for new rows
        """
        # For now, use overwrite on duplicates
        # TODO: Implement proper merge when deltalake-python supports it
        self.write(data, mode="append")


class DeltaLakeReader:
    """Read data from Delta Lake tables with time travel support."""

    def __init__(self, table_path: Path):
        """
        Initialize Delta Lake reader.

        Args:
            table_path: Path to Delta Lake table
        """
        if not DELTA_AVAILABLE:
            raise ImportError(
                "Delta Lake not available. Install with: pip install deltalake pyarrow"
            )

        self.table_path = str(table_path)
        self.table_path_obj = Path(table_path)

        if not self.table_path_obj.exists():
            raise FileNotFoundError(f"Delta table not found: {table_path}")

        self.table = DeltaTable(self.table_path)

    def read(
        self,
        columns: list[str] | None = None,
        filters: Any = None,
        version: int | None = None,
        timestamp: str | None = None
    ) -> list[dict]:
        """
        Read data from Delta Lake table.

        Args:
            columns: Columns to select (None = all)
            filters: PyArrow filter expression
            version: Table version for time travel
            timestamp: ISO timestamp for time travel

        Returns:
            List of dictionaries
        """
        # Load table at specific version if requested
        if version is not None:
            table = DeltaTable(self.table_path, version=version)
        elif timestamp is not None:
            # Convert timestamp to version (approximate)
            table = DeltaTable(self.table_path)
        else:
            table = self.table

        # Read as PyArrow table
        pa_table = table.to_pyarrow_table(columns=columns, filters=filters)

        # Convert to list of dicts
        return pa_table.to_pylist()

    def query(self, sql: str) -> list[dict]:
        """
        Query Delta Lake table using DuckDB SQL.

        Args:
            sql: SQL query string

        Returns:
            List of dictionaries
        """
        try:
            import duckdb
        except ImportError:
            raise ImportError("DuckDB required for SQL queries: pip install duckdb")

        # DuckDB can query Delta Lake tables directly
        con = duckdb.connect()
        result = con.execute(f"""
            SELECT * FROM delta_scan('{self.table_path}')
            WHERE {sql}
        """).fetchall()

        # Get column names
        columns = [desc[0] for desc in con.description]

        # Convert to list of dicts
        return [dict(zip(columns, row)) for row in result]

    def count(self) -> int:
        """Get total record count."""
        return len(self.read(columns=['_ingestion_time']))

    def get_schema(self) -> pa.Schema:
        """Get table schema."""
        return self.table.schema().to_pyarrow()

    def get_history(self) -> list[dict]:
        """Get table history (all versions)."""
        return self.table.history()

    def vacuum(self, retention_hours: int = 168):
        """
        Remove old Parquet files (default: 7 days).

        Args:
            retention_hours: Hours to retain old versions
        """
        self.table.vacuum(retention_hours=retention_hours)
        logger.info(f"Vacuumed {self.table_path} (retention={retention_hours}h)")


# Convenience functions for common tables

def write_raw_urls(data: list[dict], mode: str = "append"):
    """Write to raw_urls Delta table."""
    writer = DeltaLakeWriter(DELTA_RAW_URLS, partition_by=['crawl_date'])
    writer.write(data, mode=mode)


def write_validated_urls(data: list[dict], mode: str = "append"):
    """Write to validated_urls Delta table."""
    writer = DeltaLakeWriter(DELTA_VALIDATED_URLS, partition_by=['validation_date'])
    writer.write(data, mode=mode)


def write_enriched_content(data: list[dict], mode: str = "append"):
    """Write to enriched_content Delta table."""
    writer = DeltaLakeWriter(DELTA_ENRICHED_CONTENT, partition_by=['enrichment_date'])
    writer.write(data, mode=mode)


def read_raw_urls(filters: Any = None) -> list[dict]:
    """Read from raw_urls Delta table."""
    reader = DeltaLakeReader(DELTA_RAW_URLS)
    return reader.read(filters=filters)


def read_validated_urls(filters: Any = None) -> list[dict]:
    """Read from validated_urls Delta table."""
    reader = DeltaLakeReader(DELTA_VALIDATED_URLS)
    return reader.read(filters=filters)


def read_enriched_content(filters: Any = None) -> list[dict]:
    """Read from enriched_content Delta table."""
    reader = DeltaLakeReader(DELTA_ENRICHED_CONTENT)
    return reader.read(filters=filters)


def query_enriched_content(sql_where: str) -> list[dict]:
    """
    Query enriched content using SQL WHERE clause.

    Example:
        results = query_enriched_content("title LIKE '%admissions%'")
    """
    reader = DeltaLakeReader(DELTA_ENRICHED_CONTENT)
    return reader.query(sql_where)
