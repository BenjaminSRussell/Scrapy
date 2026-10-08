import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from src.lakehouse.lakehouse_manager import DeltaLakeManager

class TestDeltaLakeManager(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.manager = DeltaLakeManager(base_path=self.tmpdir, start_workers=False)

    def tearDown(self):
        self.manager.shutdown()
        shutil.rmtree(self.tmpdir)

    @patch("deltalake.DeltaTable")
    @patch("src.lakehouse.lakehouse_manager.pa")
    def test_export_empty_table(self, mock_pa, mock_delta_table):
        table_name = "stage1_discovery"

        mock_arrow_table = MagicMock()
        mock_arrow_table.num_rows = 0
        mock_arrow_table.schema = MagicMock()
        mock_pa.Table.from_pylist.return_value = mock_arrow_table

        output_path = Path(self.tmpdir) / "output.csv"
        result = self.manager.export(table_name, str(output_path))

        self.assertEqual(result["table"], table_name)
        self.assertEqual(result["rows"], 0)
        self.assertEqual(result["columns"], 0)
        mock_delta_table.assert_not_called()

    def test_export_non_empty_table(self):
        import pyarrow as pa
        from deltalake import write_deltalake

        table_name = "stage1_discovery"
        df = pd.DataFrame([{"col1": "a", "col2": 1}])
        write_deltalake(str(self.manager.get_table_path(table_name)), pa.Table.from_pandas(df, preserve_index=False))

        output_path = Path(self.tmpdir) / "output.csv"
        result = self.manager.export(table_name, str(output_path), format="csv")

        self.assertEqual(result["table"], table_name)
        self.assertEqual(result["rows"], len(df))
        self.assertEqual(result["columns"], len(df.columns))
        self.assertTrue(output_path.exists())

if __name__ == "__main__":
    unittest.main()
