"""Tests for Delta Lake integration to ensure data is stored correctly."""

import copy
import sys
from pathlib import Path

import pytest

# Add project root to sys.path to allow importing src modules
SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Mock DELTA_AVAILABLE to be True to run tests even if dependencies are not installed
try:
    from src.common.delta_lake import (
        DELTA_AVAILABLE,
        DeltaLakeReader,
        DeltaLakeWriter,
    )
except ImportError:
    DELTA_AVAILABLE = False
    DeltaLakeReader = None
    DeltaLakeWriter = None


# Sample data for testing
SAMPLE_DATA = [
    {"id": 1, "value": "A"},
    {"id": 2, "value": "B"},
]

ADDITIONAL_DATA = [
    {"id": 3, "value": "C"},
    {"id": 4, "value": "D"},
]

OVERWRITE_DATA = [
    {"id": 5, "value": "E"},
]


@pytest.mark.skipif(not DELTA_AVAILABLE, reason="Delta Lake dependencies not installed")
class TestDeltaLakeIntegration:
    """Test suite for DeltaLakeWriter and DeltaLakeReader."""

    def test_write_and_read_data(self, tmp_path):
        """Test writing data to a Delta table and reading it back."""
        table_path = tmp_path / "delta_table"
        writer = DeltaLakeWriter(table_path)
        writer.write(copy.deepcopy(SAMPLE_DATA))

        reader = DeltaLakeReader(table_path)
        read_data = reader.read()

        # Remove the _ingestion_time column for comparison
        for row in read_data:
            del row["_ingestion_time"]

        assert read_data == SAMPLE_DATA, "Data read should match data written."

    def test_append_data(self, tmp_path):
        """Test appending data to an existing Delta table."""
        table_path = tmp_path / "delta_table"
        writer = DeltaLakeWriter(table_path)

        # Initial write
        writer.write(copy.deepcopy(SAMPLE_DATA))

        # Append additional data
        writer.write(copy.deepcopy(ADDITIONAL_DATA), mode="append")

        reader = DeltaLakeReader(table_path)
        read_data = reader.read()

        # Remove the _ingestion_time column for comparison
        for row in read_data:
            del row["_ingestion_time"]

        expected_data = SAMPLE_DATA + ADDITIONAL_DATA
        assert sorted(d['id'] for d in read_data) == sorted(d['id'] for d in expected_data), "Appended data should be present in the table."

    def test_overwrite_data(self, tmp_path):
        """Test overwriting data in a Delta table."""
        table_path = tmp_path / "delta_table"
        writer = DeltaLakeWriter(table_path)

        # Initial write
        writer.write(copy.deepcopy(SAMPLE_DATA))

        # Overwrite with new data
        writer.write(copy.deepcopy(OVERWRITE_DATA), mode="overwrite")

        reader = DeltaLakeReader(table_path)
        read_data = reader.read()

        # Remove the _ingestion_time column for comparison
        for row in read_data:
            del row["_ingestion_time"]

        assert read_data == OVERWRITE_DATA, "Data should be overwritten."

    def test_read_with_filters(self, tmp_path):
        """Test reading data from a Delta table with filters."""
        table_path = tmp_path / "delta_table"
        writer = DeltaLakeWriter(table_path)
        writer.write(copy.deepcopy(SAMPLE_DATA))

        reader = DeltaLakeReader(table_path)

        # Define a filter to select rows where id=2
        filters = [("id", "=", 2)]
        filtered_data = reader.read(filters=filters)

        # Remove the _ingestion_time column for comparison
        for row in filtered_data:
            del row["_ingestion_time"]

        expected_data = [{"id": 2, "value": "B"}]
        assert filtered_data == expected_data, "Filtered data should match the expected subset."

    def test_non_existent_table(self, tmp_path):
        """Test that reading from a non-existent table raises an error."""
        table_path = tmp_path / "non_existent_table"
        with pytest.raises(FileNotFoundError):
            DeltaLakeReader(table_path)