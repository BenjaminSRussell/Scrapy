"""Shared test fixtures and utilities"""

import sys
from pathlib import Path
import pytest
from typing import List, Tuple

# Add src to Python path for imports
src_dir = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(src_dir))

from common.urls import canonicalize_and_hash


@pytest.fixture
def first_1000_urls() -> List[Tuple[str, str]]:
    """Generate first 1000 URLs with their hashes for testing.

    Returns list of (url, url_hash) tuples for use in pipeline tests.
    """
    urls = []

    # Generate diverse UConn URLs for testing
    base_paths = [
        '/about', '/admissions', '/academics', '/research', '/students',
        '/faculty', '/staff', '/alumni', '/athletics', '/news',
        '/events', '/library', '/housing', '/dining', '/parking',
        '/health', '/wellness', '/diversity', '/sustainability', '/global',
        '/engineering', '/business', '/law', '/medicine', '/nursing',
        '/education', '/arts', '/sciences', '/agriculture', '/pharmacy'
    ]

    # Generate sub-paths
    sub_paths = [
        '', '/overview', '/programs', '/requirements', '/application',
        '/contact', '/faculty', '/resources', '/calendar', '/policies'
    ]

    # Generate URLs
    for i in range(1000):
        base_idx = i % len(base_paths)
        sub_idx = (i // len(base_paths)) % len(sub_paths)
        page_num = i // (len(base_paths) * len(sub_paths))

        base_path = base_paths[base_idx]
        sub_path = sub_paths[sub_idx]

        if page_num > 0:
            url = f"https://uconn.edu{base_path}{sub_path}?page={page_num}"
        else:
            url = f"https://uconn.edu{base_path}{sub_path}"

        # Remove double slashes
        url = url.replace('//', '/')
        url = url.replace('http:/', 'http://')
        url = url.replace('https:/', 'https://')

        canonical_url, url_hash = canonicalize_and_hash(url)
        urls.append((canonical_url, url_hash))

    return urls


@pytest.fixture
def sample_discovery_items(first_1000_urls):
    """Generate sample DiscoveryItem objects for testing"""
    from common.schemas import DiscoveryItem
    from datetime import datetime

    items = []
    for i, (url, url_hash) in enumerate(first_1000_urls):
        item = DiscoveryItem(
            source_url="https://uconn.edu/",
            discovered_url=url,
            first_seen=datetime.now().isoformat(),
            url_hash=url_hash,
            discovery_depth=i % 5  # Vary depth from 0-4
        )
        items.append(item)

    return items


@pytest.fixture
def sample_validation_results(first_1000_urls):
    """Generate sample ValidationResult objects for testing"""
    from common.schemas import ValidationResult
    from datetime import datetime

    results = []
    for i, (url, url_hash) in enumerate(first_1000_urls):
        # Make some URLs valid and some invalid for realistic testing
        is_valid = i % 10 != 0  # 90% valid, 10% invalid
        status_code = 200 if is_valid else 404

        result = ValidationResult(
            url=url,
            url_hash=url_hash,
            status_code=status_code,
            content_type="text/html; charset=utf-8" if is_valid else "",
            content_length=1500 + (i * 10) if is_valid else 0,
            response_time=0.1 + (i * 0.001),
            is_valid=is_valid,
            error_message=None if is_valid else "Not Found",
            validated_at=datetime.now().isoformat()
        )
        results.append(result)

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