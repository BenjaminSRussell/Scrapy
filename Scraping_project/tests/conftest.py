"""Pytest configuration and shared fixtures for all tests."""

import sys
from pathlib import Path

# Add src to Python path for all tests
src_dir = Path(__file__).parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

# Add data/samples to path for test utilities
samples_dir = Path(__file__).parent.parent / "data"
if str(samples_dir) not in sys.path:
    sys.path.insert(0, str(samples_dir))

import pytest


@pytest.fixture
def temp_data_dir(tmp_path):
    """Create a temporary data directory structure for tests."""
    data_dir = tmp_path / "data"
    for subdir in ["raw", "processed/stage01", "processed/stage02", "processed/stage03", "logs"]:
        (data_dir / subdir).mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture
def sample_seed_csv(temp_data_dir):
    """Create a sample seed CSV file for testing."""
    seed_file = temp_data_dir / "raw" / "uconn_urls.csv"
    test_urls = [
        "https://uconn.edu/test1",
        "https://uconn.edu/test2",
        "https://admissions.uconn.edu/apply"
    ]

    with open(seed_file, 'w') as f:
        for url in test_urls:
            f.write(f"{url}\n")

    return seed_file