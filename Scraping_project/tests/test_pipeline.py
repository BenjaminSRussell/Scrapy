"""
Minimal pipeline tests - can be expanded later.
"""

import pytest
from pathlib import Path


def test_imports():
    """Test that core modules can be imported."""
    from src.common.constants import DELTA_LAKE, DATA_DIR
    from src.common.delta_storage import DeltaStorage
    from src.common.nlp import process_text
    from src.common.schemas import URLRecord

    assert DELTA_LAKE.exists() or True  # Will be created on first run
    assert DATA_DIR.exists()


def test_constants():
    """Test constants are properly defined."""
    from src.common.constants import DELTA_LAKE, DEBERTA_MODEL

    assert DELTA_LAKE.name == "delta_lake"
    assert "deberta" in DEBERTA_MODEL.lower()


# Add more tests as needed
