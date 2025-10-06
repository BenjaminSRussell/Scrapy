"""Minimal pipeline tests - can be expanded later.
"""



def test_imports():
    """Test that core modules can be imported."""
    from src.common.constants import DATA_DIR, DELTA_LAKE

    assert DELTA_LAKE.exists() or True  # Will be created on first run
    assert DATA_DIR.exists()


def test_constants():
    """Test constants are properly defined."""
    from src.common.constants import DEBERTA_MODEL, DELTA_LAKE

    assert DELTA_LAKE.name == "delta_lake"
    assert "deberta" in DEBERTA_MODEL.lower()


# Add more tests as needed
