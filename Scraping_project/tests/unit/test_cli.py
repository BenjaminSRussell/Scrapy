import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "Scraping_project")
import cli

def test_cli_help_exits_cleanly():
    with patch.object(sys, "argv", ["cli.py", "--help"]):
        with pytest.raises(SystemExit) as e:
            cli.main()
        assert e.value.code == 0

@pytest.mark.xfail(reason="Bug in CLI exit code handling: should exit non-zero on export failure")
@patch("src.common.delta_lake.get_delta_manager")
def test_export_non_existent_table_fails(mock_get_manager):
    mock_manager = MagicMock()
    mock_manager.export.return_value = {"error": "Table does not exist"}
    mock_get_manager.return_value = mock_manager

    with patch.object(sys, "argv", ["cli.py", "export", "--table", "non_existent_table"]):
        with pytest.raises(SystemExit) as e:
            cli.main()
        assert e.value.code != 0, "BUG: The script exited with 0 for a failed single export."

@pytest.mark.xfail(reason="Bug in CLI exit code handling: should exit non-zero on partial export failure")
@patch("src.common.delta_lake.get_delta_manager")
def test_export_all_with_failures_exits_non_zero(mock_get_manager):
    mock_manager = MagicMock()
    mock_manager.export_all.return_value = [
        {"table": "table1", "rows": 100, "size_mb": 1.2},
        {"table": "table2", "error": "Mock export failed"},
    ]
    mock_get_manager.return_value = mock_manager

    with patch.object(sys, "argv", ["cli.py", "export"]):
        with pytest.raises(SystemExit) as e:
            cli.main()

    assert e.value.code != 0, "BUG: The script exited with 0 despite a partial export failure."
