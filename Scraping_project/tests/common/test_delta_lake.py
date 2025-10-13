import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

# Ensure project root is in Python path
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

# Mock deltalake and pyarrow before importing DeltaLakeManager
mock_deltalake_module = MagicMock()
mock_pyarrow_module = MagicMock()
mock_pyarrow_csv_module = MagicMock()
mock_pyarrow_parquet_module = MagicMock()
sys.modules['deltalake'] = mock_deltalake_module
sys.modules['pyarrow'] = mock_pyarrow_module
sys.modules['pyarrow.csv'] = mock_pyarrow_csv_module
sys.modules['pyarrow.parquet'] = mock_pyarrow_parquet_module

from src.common.delta_lake import DeltaLakeManager  # noqa: E402


class TestDeltaLakeManager(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.manager = DeltaLakeManager()
        self.manager.base_path = Path(self.tmpdir)
        # Update the tables dictionary to use the temp path
        for table_name in list(self.manager.tables.keys()):
            self.manager.tables[table_name] = self.manager.base_path / table_name
        # Reset mocks before each test
        mock_deltalake_module.reset_mock()
        mock_pyarrow_module.reset_mock()
        mock_pyarrow_csv_module.reset_mock()
        mock_pyarrow_parquet_module.reset_mock()


    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_export_empty_table(self):
        """Test that exporting an empty table handles gracefully."""
        table_name = 'stage1_discovery'
        # Do not create _delta_log to simulate non-existent table

        # Configure the mock pyarrow module to return an empty table
        mock_arrow_table = MagicMock()
        mock_arrow_table.num_rows = 0
        mock_arrow_table.schema = MagicMock()
        mock_pyarrow_module.Table.from_pylist.return_value = mock_arrow_table

        output_path = Path(self.tmpdir) / "output.csv"
        result = self.manager.export(table_name, str(output_path))

        # Verify the result
        self.assertEqual(result['table'], table_name)
        self.assertEqual(result['rows'], 0)
        self.assertEqual(result['columns'], 0)

    def test_export_non_empty_table(self):
        """Test exporting a non-empty table."""
        table_name = 'stage1_discovery'
        table_path = self.manager.tables[table_name]
        (table_path / "_delta_log").mkdir(parents=True, exist_ok=True)

        # Configure the mock deltalake module to return a non-empty table
        df = pd.DataFrame([{'col1': 'a', 'col2': 1}])
        mock_table_instance = MagicMock()
        mock_arrow_table = MagicMock()
        mock_arrow_table.num_rows = len(df)
        mock_arrow_table.schema = MagicMock()
        # Set __len__ to return the number of columns
        type(mock_arrow_table.schema).__len__ = lambda x: len(df.columns)
        mock_arrow_table.to_pandas.return_value = df
        mock_table_instance.to_pyarrow_table.return_value = mock_arrow_table
        mock_deltalake_module.DeltaTable.return_value = mock_table_instance

        output_path = Path(self.tmpdir) / "output.csv"
        result = self.manager.export(table_name, str(output_path))

        # Verify the result dictionary
        self.assertEqual(result['table'], table_name)
        self.assertEqual(result['rows'], len(df))
        self.assertEqual(result['columns'], len(df.columns))


if __name__ == '__main__':
    unittest.main()
