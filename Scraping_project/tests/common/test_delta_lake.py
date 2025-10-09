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
sys.modules['deltalake'] = mock_deltalake_module
sys.modules['pyarrow'] = mock_pyarrow_module

from src.common.delta_lake import DeltaLakeManager


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


    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_export_empty_table(self):
        """Test that exporting an empty table creates a file."""
        table_name = 'stage1_discovery'
        # Do not create _delta_log to simulate non-existent table

        # Configure the mock pyarrow module to return an empty DataFrame
        mock_pyarrow_module.Table.from_pylist.return_value.to_pandas.return_value = pd.DataFrame()

        output_path = Path(self.tmpdir) / "output.csv"
        self.manager.export(table_name, str(output_path))

        self.assertTrue(output_path.is_file())
        self.assertEqual(output_path.stat().st_size, 1)

    def test_export_non_empty_table(self):
        """Test exporting a non-empty table."""
        table_name = 'stage1_discovery'
        table_path = self.manager.tables[table_name]
        (table_path / "_delta_log").mkdir(parents=True, exist_ok=True)

        # Configure the mock deltalake module to return a non-empty DataFrame
        df = pd.DataFrame([{'col1': 'a', 'col2': 1}])
        mock_table_instance = MagicMock()
        mock_arrow_table = MagicMock()
        mock_arrow_table.to_pandas.return_value = df
        mock_table_instance.to_pyarrow_table.return_value = mock_arrow_table
        mock_deltalake_module.DeltaTable.return_value = mock_table_instance

        output_path = Path(self.tmpdir) / "output.csv"
        self.manager.export(table_name, str(output_path))

        self.assertTrue(output_path.is_file())
        self.assertGreater(output_path.stat().st_size, 0)

        read_df = pd.read_csv(output_path)
        pd.testing.assert_frame_equal(read_df, df)


if __name__ == '__main__':
    unittest.main()
