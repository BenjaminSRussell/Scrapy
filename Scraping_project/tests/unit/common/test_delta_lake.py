import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from src.common.delta_lake import DeltaLakeManager

class TestDeltaLakeManagerInit:

    @pytest.mark.unit
    def test_init_creates_base_directory(self):
        test_dir = tempfile.mkdtemp(prefix="delta_test_")

        try:
            DeltaLakeManager(base_path=test_dir, start_workers=False)

            assert Path(test_dir).exists()
            assert Path(test_dir).is_dir()

        finally:
            shutil.rmtree(test_dir)

    @pytest.mark.unit
    def test_init_with_invalid_path_raises(self):
        with pytest.raises((OSError, ValueError)):
            DeltaLakeManager(base_path="/invalid/path/that/cannot/be/created", start_workers=False)

class TestDeltaLakeWrite:

    @pytest.mark.unit
    def test_write_single_row(self, delta_sandbox):
        data = [{"url": "https://example.com", "status": "success"}]

        delta_sandbox.write("test_table", data, mode="append", async_write=False)

        result = delta_sandbox.read("test_table")
        assert len(result) == 1
        assert result[0]["url"] == "https://example.com"

    @pytest.mark.unit
    def test_write_multiple_rows(self, delta_sandbox):
        data = [{"url": f"https://example.com/{i}", "status": "success"} for i in range(10)]

        delta_sandbox.write("test_table", data, mode="append", async_write=False)

        result = delta_sandbox.read("test_table")
        assert len(result) == 10

    @pytest.mark.unit
    def test_write_overwrite_mode(self, delta_sandbox):
        data1 = [{"url": "https://example.com/1", "value": 1}]
        delta_sandbox.write("test_table", data1, mode="overwrite", async_write=False)

        data2 = [{"url": "https://example.com/2", "value": 2}]
        delta_sandbox.write("test_table", data2, mode="overwrite", async_write=False)

        result = delta_sandbox.read("test_table")
        assert len(result) == 1
        assert result[0]["url"] == "https://example.com/2"

    @pytest.mark.unit
    def test_write_append_mode(self, delta_sandbox):
        data1 = [{"url": "https://example.com/1"}]
        delta_sandbox.write("test_table", data1, mode="append", async_write=False)

        data2 = [{"url": "https://example.com/2"}]
        delta_sandbox.write("test_table", data2, mode="append", async_write=False)

        result = delta_sandbox.read("test_table")
        assert len(result) == 2

    @pytest.mark.unit
    def test_write_empty_data_skips(self, delta_sandbox):
        delta_sandbox.write("test_table", [], mode="append", async_write=False)

        with pytest.raises(ValueError):
            delta_sandbox.read("test_table")

    @pytest.mark.unit
    def test_write_with_partition_columns(self, delta_sandbox):
        data = [
            {"url": "https://example.com/1", "date": "2024-01-01", "status": "success"},
            {"url": "https://example.com/2", "date": "2024-01-02", "status": "success"},
            {"url": "https://example.com/3", "date": "2024-01-01", "status": "failed"},
        ]

        delta_sandbox.write("test_table", data, mode="overwrite", partition_cols=["date", "status"], async_write=False)

        result = delta_sandbox.read("test_table")
        assert len(result) == 3

class TestDeltaLakeRead:

    @pytest.mark.unit
    def test_read_all_rows(self, delta_sandbox):
        data = [{"id": i, "value": f"row_{i}"} for i in range(5)]
        delta_sandbox.write("test_table", data, mode="overwrite", async_write=False)

        result = delta_sandbox.read("test_table")
        assert len(result) == 5

    @pytest.mark.unit
    def test_read_with_filter(self, delta_sandbox):
        data = [
            {"id": 1, "status": "success"},
            {"id": 2, "status": "failed"},
            {"id": 3, "status": "success"},
        ]
        delta_sandbox.write("test_table", data, mode="overwrite", async_write=False)

        result = delta_sandbox.read("test_table", filter="status = 'success'")
        assert len(result) == 2

    @pytest.mark.unit
    def test_read_nonexistent_table_raises(self, delta_sandbox):
        with pytest.raises(ValueError):
            delta_sandbox.read("nonexistent_table")

    @pytest.mark.unit
    def test_read_with_columns(self, delta_sandbox):
        data = [{"id": 1, "name": "test", "value": 100}]
        delta_sandbox.write("test_table", data, mode="overwrite", async_write=False)

        result = delta_sandbox.read("test_table", columns=["id", "name"])
        assert len(result) == 1
        assert "id" in result[0]
        assert "name" in result[0]
        assert "value" not in result[0]

class TestDeltaLakeBatchOperations:

    @pytest.mark.unit
    def test_batch_write_accumulates(self, delta_sandbox):
        for i in range(5):
            delta_sandbox.add_to_batch("test_table", {"id": i})

        delta_sandbox.flush_batch("test_table")

        result = delta_sandbox.read("test_table")
        assert len(result) == 5

    @pytest.mark.unit
    def test_batch_write_auto_flushes(self, delta_sandbox):
        delta_sandbox.batch_size = 10

        for i in range(15):
            delta_sandbox.add_to_batch("test_table", {"id": i})

        delta_sandbox.flush_batch("test_table")

        result = delta_sandbox.read("test_table")
        assert len(result) == 15

    @pytest.mark.unit
    def test_flush_all_batches(self, delta_sandbox):
        for i in range(5):
            delta_sandbox.add_to_batch("table1", {"id": i})
            delta_sandbox.add_to_batch("table2", {"id": i})

        delta_sandbox.flush_all_batches()

        assert len(delta_sandbox.read("table1")) == 5
        assert len(delta_sandbox.read("table2")) == 5

class TestDeltaLakeTableManagement:

    @pytest.mark.unit
    def test_list_tables(self, delta_sandbox):
        delta_sandbox.write("table1", [{"id": 1}], mode="overwrite", async_write=False)
        delta_sandbox.write("table2", [{"id": 2}], mode="overwrite", async_write=False)

        tables = delta_sandbox.list_tables()
        assert "table1" in tables
        assert "table2" in tables

    @pytest.mark.unit
    def test_table_exists(self, delta_sandbox):
        delta_sandbox.write("test_table", [{"id": 1}], mode="overwrite", async_write=False)

        assert delta_sandbox.table_exists("test_table") is True
        assert delta_sandbox.table_exists("nonexistent") is False

    @pytest.mark.unit
    def test_delete_table(self, delta_sandbox):
        delta_sandbox.write("test_table", [{"id": 1}], mode="overwrite", async_write=False)

        delta_sandbox.delete_table("test_table")

        assert delta_sandbox.table_exists("test_table") is False

    @pytest.mark.unit
    def test_get_table_schema(self, delta_sandbox):
        data = [{"id": 1, "name": "test", "value": 100.5, "active": True}]
        delta_sandbox.write("test_table", data, mode="overwrite", async_write=False)

        schema = delta_sandbox.get_table_schema("test_table")

        field_names = [field.name for field in schema]
        assert "id" in field_names
        assert "name" in field_names
        assert "value" in field_names
        assert "active" in field_names

class TestDeltaLakeTimeTravel:

    @pytest.mark.unit
    def test_read_version(self, delta_sandbox):
        delta_sandbox.write("test_table", [{"id": 1, "value": "v0"}], mode="overwrite", async_write=False)

        delta_sandbox.write("test_table", [{"id": 1, "value": "v1"}], mode="overwrite", async_write=False)

        result_v0 = delta_sandbox.read("test_table", version=0)
        assert result_v0[0]["value"] == "v0"

        result_v1 = delta_sandbox.read("test_table", version=1)
        assert result_v1[0]["value"] == "v1"

    @pytest.mark.unit
    def test_get_table_history(self, delta_sandbox):
        delta_sandbox.write("test_table", [{"id": 1}], mode="overwrite", async_write=False)
        delta_sandbox.write("test_table", [{"id": 2}], mode="overwrite", async_write=False)

        history = delta_sandbox.get_table_history("test_table")

        assert len(history) == 2

class TestDeltaLakeDataIntegrity:

    @pytest.mark.unit
    def test_schema_evolution(self, delta_sandbox):
        data1 = [{"id": 1, "name": "test"}]
        delta_sandbox.write("test_table", data1, mode="overwrite", async_write=False)

        data2 = [{"id": 2, "name": "test2", "new_field": "value"}]
        delta_sandbox.write("test_table", data2, mode="append", async_write=False)

        result = delta_sandbox.read("test_table")
        assert len(result) == 2

    @pytest.mark.unit
    def test_duplicate_handling(self, delta_sandbox):
        data = [
            {"id": 1, "value": "first"},
            {"id": 1, "value": "duplicate"},
        ]

        delta_sandbox.write("test_table", data, mode="append", async_write=False)

        result = delta_sandbox.read("test_table")
        assert len(result) == 2

    @pytest.mark.unit
    def test_null_handling(self, delta_sandbox):
        data = [
            {"id": 1, "value": "present"},
            {"id": 2, "value": None},
            {"id": 3},
        ]

        delta_sandbox.write("test_table", data, mode="append", async_write=False)

        result = delta_sandbox.read("test_table")
        assert len(result) == 3

class TestDeltaLakePerformance:

    @pytest.mark.unit
    @pytest.mark.performance
    def test_large_batch_write_performance(self, delta_sandbox, performance_timer):
        data = [{"id": i, "value": f"row_{i}", "timestamp": datetime.now().isoformat()} for i in range(1000)]

        with performance_timer as timer:
            delta_sandbox.write("test_table", data, mode="overwrite", async_write=False)

        assert timer.elapsed < 5.0, f"Write took {timer.elapsed:.2f}s, expected < 5s"

    @pytest.mark.unit
    @pytest.mark.performance
    def test_read_performance(self, delta_sandbox, performance_timer):
        data = [{"id": i} for i in range(1000)]
        delta_sandbox.write("test_table", data, mode="overwrite", async_write=False)

        with performance_timer as timer:
            result = delta_sandbox.read("test_table")

        assert len(result) == 1000
        assert timer.elapsed < 2.0, f"Read took {timer.elapsed:.2f}s, expected < 2s"
