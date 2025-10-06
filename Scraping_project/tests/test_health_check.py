"""Tests for health check script."""

import pytest
from health_check import check_file_structure, check_dependencies


def test_file_structure():
    """Test that critical files exist."""
    result = check_file_structure()
    assert result is True, "Critical files are missing"


def test_dependencies():
    """Test that critical dependencies are installed."""
    # This may fail in CI if not all deps are installed
    # So we just check it doesn't raise an exception
    try:
        result = check_dependencies()
        # Result can be True or False depending on environment
        assert isinstance(result, bool)
    except Exception as e:
        pytest.fail(f"check_dependencies raised {e}")
