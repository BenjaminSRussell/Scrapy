"""Tests for Stage 1 Discovery Spider"""

# TODO: Implement discovery spider tests
# Need to test:
# 1. Spider initialization with max_depth parameter
# 2. Seed URL loading from CSV
# 3. Link extraction and canonicalization
# 4. Depth limiting behavior
# 5. URL deduplication by hash
# 6. DiscoveryItem output format

import os
import tempfile
from pathlib import Path
from typing import List

import pytest
from unittest.mock import Mock, patch, mock_open

from stage1.discovery_spider import DiscoverySpider
from common.schemas import DiscoveryItem
from samples import html_response, build_discovery_item


def test_discovery_spider_initialization():
    """Test spider initializes with correct max_depth"""
    # Test default initialization
    spider = DiscoverySpider()
    assert spider.max_depth == 3  # Default value
    assert spider.name == "discovery"
    assert "uconn.edu" in spider.allowed_domains

    # Test custom max_depth
    spider = DiscoverySpider(max_depth=5)
    assert spider.max_depth == 5

    # Test observability counters are initialized
    assert spider.total_urls_parsed == 0
    assert spider.unique_hashes_found == 0
    assert spider.duplicates_skipped == 0
    assert spider.seed_count == 0


@patch("builtins.open", new_callable=mock_open, read_data="https://uconn.edu/test1\nhttps://uconn.edu/test2\n")
@patch("pathlib.Path.exists", return_value=True)
def test_discovery_spider_loads_seed_urls(mock_exists, mock_file):
    """Test spider loads URLs from CSV file correctly"""
    spider = DiscoverySpider()

    # Generate start requests
    requests = list(spider.start_requests())

    # Should have created requests for valid URLs
    assert len(requests) >= 1

    # Check that requests have proper metadata
    for request in requests:
        assert hasattr(request, 'meta')
        assert 'source_url' in request.meta
        assert 'depth' in request.meta
        assert 'first_seen' in request.meta
        assert request.meta['depth'] == 0  # Seeds start at depth 0


@patch("pathlib.Path.exists", return_value=False)
def test_discovery_spider_handles_missing_seed_file(mock_exists):
    """Test spider handles missing seed file gracefully"""
    spider = DiscoverySpider()

    # Should return empty iterator when seed file doesn't exist
    requests = list(spider.start_requests())
    assert len(requests) == 0


@pytest.mark.parametrize(
    "html_snippet,expected_count",
    [
        (
            """
            <html><body>
                <a href=\"https://uconn.edu/page1\">Page 1</a>
                <a href=\"https://admissions.uconn.edu/page2\">Admissions</a>
                <a href=\"https://uconn.edu/assets/image.png\">Skip Image</a>
                <a href=\"/relative\">Relative</a>
            </body></html>
            """,
            3,
        ),
        (
            """
            <html><body>
                <a href=\"https://uconn.edu/page3\">P3</a>
                <a href=\"mailto:test@uconn.edu\">Email</a>
            </body></html>
            """,
            1,
        ),
    ],
)
def test_discovery_spider_extracts_links(html_snippet, expected_count):
    """Test link extraction from HTML responses"""
    spider = DiscoverySpider(max_depth=2)
    response = html_response("https://uconn.edu/test", html_snippet, depth=0)

    # Process the response (exercises real link extraction)
    results = list(spider.parse(response))

    # Should have discovered items and new requests
    discovery_items = [r for r in results if isinstance(r, DiscoveryItem)]
    new_requests = [r for r in results if hasattr(r, 'url') and not isinstance(r, DiscoveryItem)]

    # Verify we found some links (exact count depends on LinkExtractor filtering)
    assert len(discovery_items) == expected_count

    # Verify discovery items have required fields
    for item in discovery_items:
        assert hasattr(item, 'source_url')
        assert hasattr(item, 'discovered_url')
        assert hasattr(item, 'first_seen')
        assert hasattr(item, 'url_hash')
        assert hasattr(item, 'discovery_depth')

        # Verify the source URL matches
        assert item.source_url == 'https://uconn.edu/test'

        # Verify discovered URLs are valid
        assert item.discovered_url.startswith('http')

        # Verify depth is correct (next depth = current + 1)
        assert item.discovery_depth == 1


def test_discovery_spider_respects_depth_limit():
    """Test spider stops crawling at max_depth"""
    spider = DiscoverySpider(max_depth=1)

    # Create real HtmlResponse at max depth
    response = html_response(
        "https://uconn.edu/test",
        """
        <html><body>
            <a href='https://uconn.edu/page1'>Should Not Follow</a>
            <a href='https://uconn.edu/page2'>Should Not Follow</a>
        </body></html>
        """,
        depth=1,
    )

    results = list(spider.parse(response))

    # Should still generate discovery items but no new requests
    discovery_items = [r for r in results if isinstance(r, DiscoveryItem)]
    new_requests = [r for r in results if hasattr(r, 'url') and not isinstance(r, DiscoveryItem)]

    # Should have discovery items but no new requests (depth limit reached)
    assert len(discovery_items) == 2  # Still records discovered URLs
    assert len(new_requests) == 0


def test_discovery_spider_deduplicates_urls():
    """Test URL deduplication by hash"""
    spider = DiscoverySpider()

    # Create real HtmlResponse with duplicate links
    response = html_response(
        "https://uconn.edu/test",
        """
        <html><body>
            <a href='https://uconn.edu/page1'>Page 1</a>
            <a href='https://uconn.edu/page1'>Duplicate Page 1</a>
            <a href='https://uconn.edu/page2'>Page 2</a>
        </body></html>
        """,
        depth=0,
    )

    # First parse
    results1 = list(spider.parse(response))
    discovery_items1 = [r for r in results1 if isinstance(r, DiscoveryItem)]

    # Second parse of same response (should deduplicate)
    results2 = list(spider.parse(response))
    discovery_items2 = [r for r in results2 if isinstance(r, DiscoveryItem)]

    # On second parse, duplicates should be detected
    assert spider.duplicates_skipped > 0

    # URL hashes should be tracked
    assert len(spider.url_hashes) > 0

    # Second parse should yield fewer (or zero) new discovery items
    assert len(discovery_items2) <= len(discovery_items1)

def test_discovery_spider_start_requests_real_seed_file():
    """Ensure start_requests processes the real seed CSV without modification."""
    seed_path = Path(__file__).parent.parent / "data" / "raw" / "uconn_urls.csv"
    if not seed_path.exists():
        pytest.skip("Real seed CSV not available")

    spider = DiscoverySpider()
    requests = list(spider.start_requests())

    with seed_path.open("r", encoding="utf-8") as handle:
        expected_urls = [line.strip() for line in handle if line.strip()]

    assert len(requests) == len(expected_urls)
    if expected_urls:
        first_request = requests[0]
        assert first_request.url.startswith("https://")
        assert first_request.meta["depth"] == 0
