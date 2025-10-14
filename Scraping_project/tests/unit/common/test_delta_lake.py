"""Unit tests for DeltaLakeManager.

Tests Delta Lake table operations, batch writing, and data integrity.
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

from src.common.delta_lake import DeltaLakeManager


class TestDeltaLakeManagerInit:
    """Test DeltaLakeManager initialization."""

    @pytest.mark.unit
    def test_init_creates_base_directory(self):
        """Test initialization creates base directory."""
        test_dir = tempfile.mkdtemp(prefix="delta_test_")

        try:
            manager = DeltaLakeManager(base_path=test_dir, start_workers=False)

            assert Path(test_dir).exists()
            assert Path(test_dir).is_dir()

        finally:
            shutil.rmtree(test_dir)

    @pytest.mark.unit
    def test_init_with_invalid_path_raises(self):
        """Test initialization with invalid path raises error."""
        with pytest.raises((OSError, ValueError)):
            DeltaLakeManager(base_path="/invalid/path/that/cannot/be/created", start_workers=False)


class TestDeltaLakeWrite:
    """Test writing data to Delta Lake tables."""

    @pytest.mark.unit
    def test_write_single_row(self, delta_sandbox):
        """Test writing a single row to table."""
        data = [{'url': 'https://example.com', 'status': 'success'}]

        delta_sandbox.write('test_table', data, mode='append', async_write=False)

        # Read back and verify
        result = delta_sandbox.read('test_table')
        assert len(result) == 1
        assert result[0]['url'] == 'https://example.com'

    @pytest.mark.unit
    def test_write_multiple_rows(self, delta_sandbox):
        """Test writing multiple rows."""
        data = [
            {'url': f'https://example.com/{i}', 'status': 'success'}
            for i in range(10)
        ]

        delta_sandbox.write('test_table', data, mode='append', async_write=False)

        result = delta_sandbox.read('test_table')
        assert len(result) == 10

    @pytest.mark.unit
    def test_write_overwrite_mode(self, delta_sandbox):
        """Test overwrite mode replaces existing data."""
        # Write initial data
        data1 = [{'url': 'https://example.com/1', 'value': 1}]
        delta_sandbox.write('test_table', data1, mode='overwrite', async_write=False)

        # Overwrite with new data
        data2 = [{'url': 'https://example.com/2', 'value': 2}]
        delta_sandbox.write('test_table', data2, mode='overwrite', async_write=False)

        # Should only have data2
        result = delta_sandbox.read('test_table')
        assert len(result) == 1
        assert result[0]['url'] == 'https://example.com/2'

    @pytest.mark.unit
    def test_write_append_mode(self, delta_sandbox):
        """Test append mode adds to existing data."""
        # Write initial data
        data1 = [{'url': 'https://example.com/1'}]
        delta_sandbox.write('test_table', data1, mode='append', async_write=False)

        # Append more data
        data2 = [{'url': 'https://example.com/2'}]
        delta_sandbox.write('test_table', data2, mode='append', async_write=False)

        # Should have both
        result = delta_sandbox.read('test_table')
        assert len(result) == 2

    @pytest.mark.unit
    def test_write_empty_data_skips(self, delta_sandbox):
        """Test writing empty data does nothing."""
        delta_sandbox.write('test_table', [], mode='append', async_write=False)

        # Table should not exist or be empty
        with pytest.raises(Exception):
            delta_sandbox.read('test_table')

    @pytest.mark.unit
    def test_write_with_partition_columns(self, delta_sandbox):
        """Test writing with partition columns."""
        data = [
            {'url': 'https://example.com/1', 'date': '2024-01-01', 'status': 'success'},
            {'url': 'https://example.com/2', 'date': '2024-01-02', 'status': 'success'},
            {'url': 'https://example.com/3', 'date': '2024-01-01', 'status': 'failed'},
        ]

        delta_sandbox.write(
            'test_table',
            data,
            mode='overwrite',
            partition_cols=['date', 'status'],
            async_write=False
        )

        result = delta_sandbox.read('test_table')
        assert len(result) == 3


class TestDeltaLakeRead:
    """Test reading data from Delta Lake tables."""

    @pytest.mark.unit
    def test_read_all_rows(self, delta_sandbox):
        """Test reading all rows from table."""
        # Write test data
        data = [{'id': i, 'value': f'row_{i}'} for i in range(5)]
        delta_sandbox.write('test_table', data, mode='overwrite', async_write=False)

        # Read all
        result = delta_sandbox.read('test_table')
        assert len(result) == 5

    @pytest.mark.unit
    def test_read_with_filter(self, delta_sandbox):
        """Test reading with filter condition."""
        # Write test data
        data = [
            {'id': 1, 'status': 'success'},
            {'id': 2, 'status': 'failed'},
            {'id': 3, 'status': 'success'},
        ]
        delta_sandbox.write('test_table', data, mode='overwrite', async_write=False)

        # Read with filter
        result = delta_sandbox.read('test_table', filter="status = 'success'")
        assert len(result) == 2

    @pytest.mark.unit
    def test_read_nonexistent_table_raises(self, delta_sandbox):
        """Test reading nonexistent table raises error."""
        with pytest.raises(Exception):
            delta_sandbox.read('nonexistent_table')

    @pytest.mark.unit
    def test_read_with_columns(self, delta_sandbox):
        """Test reading specific columns."""
        # Write test data
        data = [{'id': 1, 'name': 'test', 'value': 100}]
        delta_sandbox.write('test_table', data, mode='overwrite', async_write=False)

        # Read specific columns
        result = delta_sandbox.read('test_table', columns=['id', 'name'])
        assert len(result) == 1
        assert 'id' in result[0]
        assert 'name' in result[0]
        assert 'value' not in result[0]


class TestDeltaLakeBatchOperations:
    """Test batch writing operations."""

    @pytest.mark.unit
    def test_batch_write_accumulates(self, delta_sandbox):
        """Test batch write accumulates items before writing."""
        # Add items to batch (below threshold)
        for i in range(5):
            delta_sandbox.add_to_batch('test_table', {'id': i})

        # Batch should not be written yet
        # (depending on implementation, might need to flush)
        delta_sandbox.flush_batch('test_table')

        result = delta_sandbox.read('test_table')
        assert len(result) == 5

    @pytest.mark.unit
    def test_batch_write_auto_flushes(self, delta_sandbox):
        """Test batch automatically flushes when threshold reached."""
        # Set small batch size
        delta_sandbox.batch_size = 10

        # Add more items than batch size
        for i in range(15):
            delta_sandbox.add_to_batch('test_table', {'id': i})

        # Should have auto-flushed at least once
        # Flush remaining
        delta_sandbox.flush_batch('test_table')

        result = delta_sandbox.read('test_table')
        assert len(result) == 15

    @pytest.mark.unit
    def test_flush_all_batches(self, delta_sandbox):
        """Test flushing all batches across tables."""
        # Add to multiple tables
        for i in range(5):
            delta_sandbox.add_to_batch('table1', {'id': i})
            delta_sandbox.add_to_batch('table2', {'id': i})

        # Flush all
        delta_sandbox.flush_all_batches()

        # Verify both tables written
        assert len(delta_sandbox.read('table1')) == 5
        assert len(delta_sandbox.read('table2')) == 5


class TestDeltaLakeTableManagement:
    """Test table management operations."""

    @pytest.mark.unit
    def test_list_tables(self, delta_sandbox):
        """Test listing all tables."""
        # Create multiple tables
        delta_sandbox.write('table1', [{'id': 1}], mode='overwrite', async_write=False)
        delta_sandbox.write('table2', [{'id': 2}], mode='overwrite', async_write=False)

        tables = delta_sandbox.list_tables()
        assert 'table1' in tables
        assert 'table2' in tables

    @pytest.mark.unit
    def test_table_exists(self, delta_sandbox):
        """Test checking if table exists."""
        delta_sandbox.write('test_table', [{'id': 1}], mode='overwrite', async_write=False)

        assert delta_sandbox.table_exists('test_table') is True
        assert delta_sandbox.table_exists('nonexistent') is False

    @pytest.mark.unit
    def test_delete_table(self, delta_sandbox):
        """Test deleting a table."""
        delta_sandbox.write('test_table', [{'id': 1}], mode='overwrite', async_write=False)

        # Delete table
        delta_sandbox.delete_table('test_table')

        # Should not exist anymore
        assert delta_sandbox.table_exists('test_table') is False

    @pytest.mark.unit
    def test_get_table_schema(self, delta_sandbox):
        """Test getting table schema."""
        data = [
            {'id': 1, 'name': 'test', 'value': 100.5, 'active': True}
        ]
        delta_sandbox.write('test_table', data, mode='overwrite', async_write=False)

        schema = delta_sandbox.get_table_schema('test_table')

        # Verify schema contains expected fields
        field_names = [field.name for field in schema.fields]
        assert 'id' in field_names
        assert 'name' in field_names
        assert 'value' in field_names
        assert 'active' in field_names


class TestDeltaLakeTimeTravel:
    """Test Delta Lake time travel capabilities."""

    @pytest.mark.unit
    def test_read_version(self, delta_sandbox):
        """Test reading specific table version."""
        # Write version 0
        delta_sandbox.write('test_table', [{'id': 1, 'value': 'v0'}], mode='overwrite', async_write=False)

        # Write version 1
        delta_sandbox.write('test_table', [{'id': 1, 'value': 'v1'}], mode='overwrite', async_write=False)

        # Read version 0
        result_v0 = delta_sandbox.read('test_table', version=0)
        assert result_v0[0]['value'] == 'v0'

        # Read version 1 (current)
        result_v1 = delta_sandbox.read('test_table', version=1)
        assert result_v1[0]['value'] == 'v1'

    @pytest.mark.unit
    def test_get_table_history(self, delta_sandbox):
        """Test getting table history."""
        # Create multiple versions
        delta_sandbox.write('test_table', [{'id': 1}], mode='overwrite', async_write=False)
        delta_sandbox.write('test_table', [{'id': 2}], mode='overwrite', async_write=False)

        history = delta_sandbox.get_table_history('test_table')

        # Should have 2 versions
        assert len(history) == 2


class TestDeltaLakeDataIntegrity:
    """Test data integrity and validation."""

    @pytest.mark.unit
    def test_schema_evolution(self, delta_sandbox):
        """Test schema evolution when adding new columns."""
        # Write initial data
        data1 = [{'id': 1, 'name': 'test'}]
        delta_sandbox.write('test_table', data1, mode='overwrite', async_write=False)

        # Write data with additional column
        data2 = [{'id': 2, 'name': 'test2', 'new_field': 'value'}]
        delta_sandbox.write('test_table', data2, mode='append', async_write=False)

        # Read and verify both rows exist
        result = delta_sandbox.read('test_table')
        assert len(result) == 2

    @pytest.mark.unit
    def test_duplicate_handling(self, delta_sandbox):
        """Test handling of duplicate data."""
        data = [
            {'id': 1, 'value': 'first'},
            {'id': 1, 'value': 'duplicate'},
        ]

        delta_sandbox.write('test_table', data, mode='append', async_write=False)

        result = delta_sandbox.read('test_table')
        # Both should be written (Delta Lake doesn't deduplicate by default)
        assert len(result) == 2

    @pytest.mark.unit
    def test_null_handling(self, delta_sandbox):
        """Test handling of null values."""
        data = [
            {'id': 1, 'value': 'present'},
            {'id': 2, 'value': None},
            {'id': 3},  # Missing 'value' key
        ]

        delta_sandbox.write('test_table', data, mode='append', async_write=False)

        result = delta_sandbox.read('test_table')
        assert len(result) == 3


class TestDeltaLakePerformance:
    """Test performance-related operations."""

    @pytest.mark.unit
    @pytest.mark.performance
    def test_large_batch_write_performance(self, delta_sandbox, performance_timer):
        """Test performance of large batch writes."""
        # Generate large dataset
        data = [
            {'id': i, 'value': f'row_{i}', 'timestamp': datetime.now().isoformat()}
            for i in range(1000)
        ]

        with performance_timer() as timer:
            delta_sandbox.write('test_table', data, mode='overwrite', async_write=False)

        # Should complete in reasonable time (adjust threshold as needed)
        assert timer.elapsed < 5.0, f"Write took {timer.elapsed:.2f}s, expected < 5s"

    @pytest.mark.unit
    @pytest.mark.performance
    def test_read_performance(self, delta_sandbox, performance_timer):
        """Test read performance."""
        # Write data
        data = [{'id': i} for i in range(1000)]
        delta_sandbox.write('test_table', data, mode='overwrite', async_write=False)

        with performance_timer() as timer:
            result = delta_sandbox.read('test_table')

        assert len(result) == 1000
        assert timer.elapsed < 2.0, f"Read took {timer.elapsed:.2f}s, expected < 2s"
