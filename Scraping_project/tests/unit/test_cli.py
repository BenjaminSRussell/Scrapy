import sys
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path to allow importing 'cli'
sys.path.insert(0, 'Scraping_project')
import cli


def test_cli_help_exits_cleanly():
    """Verify that `cli.py --help` exits with code 0."""
    with patch.object(sys, 'argv', ['cli.py', '--help']):
        with pytest.raises(SystemExit) as e:
            cli.main()
        assert e.value.code == 0

@patch('src.common.delta_lake.get_delta_manager')
def test_export_non_existent_table_fails(mock_get_manager):
    """Verify `export --table non_existent` exits with 0 (the bug)."""
    mock_manager = MagicMock()
    mock_manager.export.return_value = {'error': 'Table does not exist'}
    mock_get_manager.return_value = mock_manager

    with patch.object(sys, 'argv', ['cli.py', 'export', '--table', 'non_existent_table']):
        with pytest.raises(SystemExit) as e:
            cli.main()
        # This will fail, proving the bug, because the script exits with 0
        assert e.value.code != 0, "BUG: The script exited with 0 for a failed single export."

@patch('src.common.delta_lake.get_delta_manager')
def test_export_all_with_failures_exits_non_zero(mock_get_manager):
    """Verify `export` (all) exits with 0 (the bug) even if a table fails."""
    # Configure the mock manager to simulate a failed export
    mock_manager = MagicMock()
    # Correctly mock the success case to prevent KeyError
    mock_manager.export_all.return_value = [
        {'table': 'table1', 'rows': 100, 'size_mb': 1.2},
        {'table': 'table2', 'error': 'Mock export failed'}
    ]
    mock_get_manager.return_value = mock_manager

    # Run the export command for all tables
    with patch.object(sys, 'argv', ['cli.py', 'export']):
        with pytest.raises(SystemExit) as e:
            cli.main()

    # This assertion will fail, proving the bug. The script exits with 0.
    assert e.value.code != 0, "BUG: The script exited with 0 despite a partial export failure."
