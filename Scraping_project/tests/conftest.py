"""Shared test fixtures and utilities"""

import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple

import pytest

# Add src to Python path for imports
REPO_ROOT = Path(__file__).parent.parent
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from common.urls import canonicalize_and_hash


def _iter_seed_urls(limit: int = 1000) -> List[str]:
    """Load URLs from real seed CSVs when available."""
    candidates = [
        REPO_ROOT / "data" / "raw" / "uconn_urls.csv",
        Path(__file__).parent / "fixtures" / "sample_urls.csv",
    ]

    collected: List[str] = []
    for candidate in candidates:
        if not candidate.exists():
            continue

        with candidate.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            for row in reader:
                if not row:
                    continue
                url = row[0].strip()
                if not url:
                    continue
                collected.append(url)
                if len(collected) >= limit:
                    return collected

    return collected


@pytest.fixture
def first_1000_urls() -> List[Tuple[str, str]]:
    """Return canonicalised URLs with hashes, prioritising real data."""
    urls = _iter_seed_urls(limit=1000)

    if len(urls) < 1000:
        base_paths = [
            "/about",
            "/admissions",
            "/academics",
            "/research",
            "/students",
        ]
        while len(urls) < 1000:
            idx = len(urls)
            urls.append(f"https://uconn.edu{base_paths[idx % len(base_paths)]}/{idx}")

    pairs: List[Tuple[str, str]] = []
    for url in urls[:1000]:
        canonical_url, url_hash = canonicalize_and_hash(url)
        pairs.append((canonical_url, url_hash))

    return pairs


@pytest.fixture
def sample_discovery_items(first_1000_urls):
    """Materialise DiscoveryItem objects based on canonical URLs."""
    from common.schemas import DiscoveryItem

    base_ts = datetime(2024, 1, 1, 0, 0, 0)
    items = []
    for idx, (url, url_hash) in enumerate(first_1000_urls):
        items.append(
            DiscoveryItem(
                source_url="https://uconn.edu/",
                discovered_url=url,
                first_seen=(base_ts + timedelta(seconds=idx)).isoformat(),
                url_hash=url_hash,
                discovery_depth=idx % 5,
            )
        )
    return items


@pytest.fixture
def sample_validation_results(first_1000_urls):
    """Create ValidationResult objects demonstrating mixed outcomes."""
    from common.schemas import ValidationResult

    results = []
    for idx, (url, url_hash) in enumerate(first_1000_urls):
        is_valid = idx % 7 != 0
        status_code = 200 if is_valid else 500
        content_type = "text/html; charset=utf-8" if is_valid else "application/octet-stream"

        results.append(
            ValidationResult(
                url=url,
                url_hash=url_hash,
                status_code=status_code,
                content_type=content_type,
                content_length=2048 + idx,
                response_time=0.05 * (idx % 4 + 1),
                is_valid=is_valid,
                error_message=None if is_valid else "Simulated server error",
                validated_at=datetime.utcnow().isoformat(),
            )
        )

    return results


@pytest.fixture
def temp_jsonl_file(tmp_path):
    """Create a temporary JSONL file for testing"""
    return tmp_path / "test_output.jsonl"


@pytest.fixture
def sample_config():
    """Sample configuration for testing"""
    return {
        'scrapy': {
            'concurrent_requests': 32,
            'download_delay': 0.1,
            'download_timeout': 10,
            'user_agent': 'Test-Spider/1.0'
        },
        'stages': {
            'discovery': {
                'max_depth': 3,
                'batch_size': 100,
                'output_file': 'data/processed/stage01/new_urls.jsonl'
            },
            'validation': {
                'max_workers': 10,
                'timeout': 5,
                'output_file': 'data/processed/stage02/validated_urls.jsonl'
            }
        }
    }
