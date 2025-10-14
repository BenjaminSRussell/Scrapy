import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

# Ensure project root is in Python path
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from src.common.delta_lake import DeltaLakeManager


class TestDeltaLakeManager(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Instantiate the real manager; test methods will patch its dependencies
        # start_workers=False is crucial for tests to avoid background threads
        self.manager = DeltaLakeManager(base_path=self.tmpdir, start_workers=False)

    def tearDown(self):
        # Gracefully shut down to ensure no resources are left hanging
        self.manager.shutdown()
        shutil.rmtree(self.tmpdir)

    @patch('deltalake.DeltaTable')
    @patch('src.common.delta_lake.pa')
    def test_export_empty_table(self, mock_pa, mock_delta_table):
        """Test that exporting an empty table handles gracefully."""
        table_name = 'stage1_discovery'
        # Simulate an empty/non-existent table by not creating a _delta_log

        # Configure mock for pyarrow to return an empty table
        mock_arrow_table = MagicMock()
        mock_arrow_table.num_rows = 0
        mock_arrow_table.schema = MagicMock()
        mock_pa.Table.from_pylist.return_value = mock_arrow_table

        output_path = Path(self.tmpdir) / "output.csv"
        result = self.manager.export(table_name, str(output_path))

        # Verify the result
        self.assertEqual(result['table'], table_name)
        self.assertEqual(result['rows'], 0)
        self.assertEqual(result['columns'], 0)
        # Check that we did not try to read from a delta table because _delta_log doesn't exist
        mock_delta_table.assert_not_called()

    @patch('src.common.delta_lake.pa_csv')
    @patch('deltalake.DeltaTable')
    def test_export_non_empty_table(self, mock_delta_table, mock_pa_csv):
        """Test exporting a non-empty table."""
        table_name = 'stage1_discovery'
        table_path = self.manager.tables[table_name]
        (table_path / "_delta_log").mkdir(parents=True, exist_ok=True)

        # Configure mock for deltalake to return a non-empty table
        df = pd.DataFrame([{'col1': 'a', 'col2': 1}])
        mock_table_instance = MagicMock()
        # Import pyarrow here, as it's mocked at the module level
        import pyarrow as pa
        mock_arrow_table = pa.Table.from_pandas(df)

        mock_table_instance.to_pyarrow_table.return_value = mock_arrow_table
        mock_delta_table.return_value = mock_table_instance

        output_path = Path(self.tmpdir) / "output.csv"
        result = self.manager.export(table_name, str(output_path), format='csv')

        # Verify the result dictionary
        self.assertEqual(result['table'], table_name)
        self.assertEqual(result['rows'], len(df))
        self.assertEqual(result['columns'], len(df.columns))


if __name__ == '__main__':
    unittest.main()
