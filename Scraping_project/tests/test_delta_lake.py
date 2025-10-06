"""Tests for Delta Lake manager."""

import pytest

from src.common.delta_lake import DeltaLakeManager


def test_delta_manager_initialization():
    """Test Delta Lake manager can be initialized."""
    try:
        manager = DeltaLakeManager()
        assert manager is not None
        assert manager.base_path.exists()
    except ImportError:
        pytest.skip("Delta Lake not installed")


def test_delta_manager_tables():
    """Test Delta Lake manager has required tables."""
    try:
        manager = DeltaLakeManager()

        expected_tables = [
            'stage1_discovery',
            'stage1_errors',
            'stage1_js_render_queue',
            'stage2_page_analysis',
            'stage3_analytics',
            'stage4_large_docs',
            'stage4_summaries',
        ]

        for table_name in expected_tables:
            assert table_name in manager.tables
            assert manager.tables[table_name].exists()

    except ImportError:
        pytest.skip("Delta Lake not installed")
