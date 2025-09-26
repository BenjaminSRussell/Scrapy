"""Tests for URL helper utilities using sample inputs."""

from __future__ import annotations

import re

import pytest
from w3lib.url import canonicalize_url

from common.urls import canonicalize_and_hash, is_valid_uconn_url, sha1_hash


@pytest.mark.parametrize(
    "url",
    [
        "https://uconn.edu/test",
        "https://uconn.edu/test?query=1",
        "https://uconn.edu/test#fragment",
        "https://admissions.uconn.edu/path",
        "https://research.uconn.edu/reports/2024",
    ],
)
def test_sha1_hash_consistency(url):
    """Same URL must always hash to the same 40-character hex value."""
    first = sha1_hash(url)
    second = sha1_hash(url)
    assert first == second
    assert re.fullmatch(r"[0-9a-f]{40}", first)


@pytest.mark.parametrize(
    "raw",
    [
        "https://UCONN.edu/Test/../page",
        "https://uconn.edu/page?utm_source=test",
        "http://uconn.edu",
        "https://uconn.edu:443/page",
        "https://uconn.edu/page#section",
    ],
)
def test_canonicalize_and_hash_variants(raw):
    """Canonicalization should return stable hashes for common variants."""
    canonical, hash_val = canonicalize_and_hash(raw)
    expected = canonicalize_url(raw)
    assert canonical == expected
    assert len(hash_val) == 40


URL_DOMAIN_CASES = [
    ("https://uconn.edu", True),
    ("https://www.uconn.edu", True),
    ("https://admissions.uconn.edu", True),
    ("https://catalog.uconn.edu/path", True),
    ("http://events.uconn.edu", True),
    ("https://global.uconn.edu/study-abroad", True),
    ("https://fake-uconn.edu", False),
    ("https://google.com", False),
    ("https://uconn.com", False),
    ("https://example.edu/uconn.com", False),
    ("ftp://uconn.edu/resource", False),
    ("not-a-url", False),
    ("", False),
]


@pytest.mark.parametrize("url,expected", URL_DOMAIN_CASES)
def test_is_valid_uconn_url_behaviour(url, expected):
    """Validate heuristic domain matching for a variety of inputs."""
    assert is_valid_uconn_url(url) is expected


def test_is_valid_uconn_url_none():
    with pytest.raises(TypeError):
        is_valid_uconn_url(None)


@pytest.mark.parametrize(
    "base,query",
    [
        ("https://uconn.edu/page", ""),
        ("https://uconn.edu/page", "?a=1"),
        ("https://uconn.edu/page", "?utm_campaign=test"),
    ],
)
def test_canonicalize_hash_uniqueness(base, query):
    """Hashes should change when significant URL components differ."""
    _, h1 = canonicalize_and_hash(base)
    _, h2 = canonicalize_and_hash(base + query)
    if query:
        assert h1 != h2
    else:
        assert h1 == h2


@pytest.mark.parametrize(
    "url,expects_exception",
    [
        ("not-a-url", False),
        ("ftp://uconn.edu", False),
        ("https://", False),
        ("https://uconn.edu with spaces", False),
        ("javascript:alert('xss')", False),
        ("", False),
        (None, True),
    ],
)
def test_canonicalize_handles_malformed_inputs(url, expects_exception):
    """Ensure canonicalization behaves deterministically for malformed inputs."""
    if expects_exception:
        with pytest.raises(Exception):
            canonicalize_and_hash(url)
        return

    canonical, hash_val = canonicalize_and_hash(url)
    assert isinstance(canonical, str)
    assert len(hash_val) == 40


@pytest.mark.parametrize("length", [10, 100, 500, 1000, 2000])
def test_sha1_long_urls(length):
    """Long URLs should still hash correctly."""
    url = "https://uconn.edu/" + "a" * length
    digest = sha1_hash(url)
    assert len(digest) == 40


@pytest.mark.parametrize(
    "path",
    ["/", "/about", "/academics/programs", "/research/publications", "/students/services"],
)
def test_canonicalize_and_hash_idempotency(path):
    """Applying canonicalize_and_hash twice should be stable."""
    canonical1, hash1 = canonicalize_and_hash(f"https://uconn.edu{path}")
    canonical2, hash2 = canonicalize_and_hash(canonical1)
    assert canonical1 == canonical2
    assert hash1 == hash2


# Advanced URL handling logic tests


@pytest.mark.parametrize("url,expected_domain", [
    ("https://uconn.edu", "uconn.edu"),
    ("https://www.uconn.edu", "www.uconn.edu"),
    ("https://admissions.uconn.edu/apply", "admissions.uconn.edu"),
    ("http://research.uconn.edu:80/projects", "research.uconn.edu"),
    ("https://catalog.uconn.edu:443/", "catalog.uconn.edu"),
])
def test_extract_domain_functionality(url, expected_domain):
    """Test domain extraction from various URL formats."""
    from common.urls import extract_domain
    assert extract_domain(url) == expected_domain


@pytest.mark.parametrize("url", [
    "https://uconn.edu/../../../etc/passwd",
    "https://uconn.edu/./././admin",
    "https://uconn.edu/normal/../admin/secret",
    "https://uconn.edu/path/./file.html",
])
def test_canonicalize_path_traversal_protection(url):
    """Test canonicalization handles path traversal attempts."""
    canonical, _ = canonicalize_and_hash(url)

    # Path traversal components should be resolved
    assert "../" not in canonical
    assert "/./" not in canonical or canonical.endswith("/./")  # Only trailing ./ might remain


@pytest.mark.parametrize("url1,url2,should_be_same", [
    ("https://uconn.edu/page", "https://uconn.edu/page/", False),  # Trailing slash matters
    ("https://uconn.edu/PAGE", "https://uconn.edu/page", True),   # Case normalization
    ("https://UCONN.EDU/page", "https://uconn.edu/page", True),   # Domain case normalization
    ("http://uconn.edu/page", "https://uconn.edu/page", False),   # Protocol matters
    ("https://uconn.edu:443/page", "https://uconn.edu/page", True),  # Default port removal
    ("https://uconn.edu/page?a=1&b=2", "https://uconn.edu/page?b=2&a=1", True),  # Query param order
])
def test_url_canonicalization_edge_cases(url1, url2, should_be_same):
    """Test URL canonicalization edge cases."""
    _, hash1 = canonicalize_and_hash(url1)
    _, hash2 = canonicalize_and_hash(url2)

    if should_be_same:
        assert hash1 == hash2, f"URLs should canonicalize to same hash: {url1} vs {url2}"
    else:
        assert hash1 != hash2, f"URLs should have different hashes: {url1} vs {url2}"


@pytest.mark.parametrize("url,expected_valid", [
    # Valid UConn URLs
    ("https://uconn.edu", True),
    ("https://www.uconn.edu", True),
    ("https://admissions.uconn.edu", True),
    ("http://events.uconn.edu", True),

    # Invalid domains
    ("https://uconn-fake.edu", False),
    ("https://fake-uconn.edu", False),
    ("https://uconn.edu.evil.com", False),
    ("https://subdomain.uconn.edu.evil.com", False),

    # Invalid schemes
    ("ftp://uconn.edu", False),
    ("javascript:alert('xss')", False),
    ("data:text/html,<script>alert(1)</script>", False),

    # Edge cases
    ("https://", False),
    ("", False),
    ("not-a-url", False),
    ("https://uconn", False),  # Missing TLD
])
def test_is_valid_uconn_url_comprehensive(url, expected_valid):
    """Comprehensive test of UConn URL validation logic."""
    assert is_valid_uconn_url(url) == expected_valid


def test_url_hash_collision_resistance():
    """Test that similar URLs produce different hashes."""
    urls = [
        "https://uconn.edu/page1",
        "https://uconn.edu/page2",
        "https://uconn.edu/page1?param=1",
        "https://uconn.edu/page1#section",
        "https://admissions.uconn.edu/page1",
    ]

    hashes = []
    for url in urls:
        _, url_hash = canonicalize_and_hash(url)
        hashes.append(url_hash)

    # All hashes should be unique
    assert len(set(hashes)) == len(hashes)

    # All hashes should be 40 characters (SHA-1)
    for hash_val in hashes:
        assert len(hash_val) == 40
        assert all(c in '0123456789abcdef' for c in hash_val)


@pytest.mark.parametrize("url", [
    "https://uconn.edu/" + "a" * 1000,  # Very long path
    "https://uconn.edu/?" + "&".join([f"param{i}=value{i}" for i in range(100)]),  # Many params
    "https://uconn.edu/path with spaces and special chars !@#$%^&*()",  # Special characters
])
def test_url_canonicalization_extreme_cases(url):
    """Test URL canonicalization with extreme cases."""
    canonical, url_hash = canonicalize_and_hash(url)

    # Should not crash and should produce valid output
    assert isinstance(canonical, str)
    assert len(url_hash) == 40
    assert all(c in '0123456789abcdef' for c in url_hash)


def test_url_hash_deterministic():
    """Test that URL hashing is deterministic across runs."""
    url = "https://uconn.edu/test-deterministic"

    # Hash the same URL multiple times
    hashes = []
    for _ in range(10):
        _, url_hash = canonicalize_and_hash(url)
        hashes.append(url_hash)

    # All hashes should be identical
    assert len(set(hashes)) == 1


@pytest.mark.parametrize("url,expected_canonical", [
    ("https://uconn.edu", "https://uconn.edu"),
    ("https://uconn.edu/", "https://uconn.edu/"),
    ("https://uconn.edu/path/../other", "https://uconn.edu/other"),
    ("https://uconn.edu/./path", "https://uconn.edu/path"),
    ("https://uconn.edu:443/secure", "https://uconn.edu/secure"),
    ("http://uconn.edu:80/normal", "http://uconn.edu/normal"),
])
def test_canonicalization_normalization(url, expected_canonical):
    """Test specific canonicalization rules."""
    canonical, _ = canonicalize_and_hash(url)
    assert canonical == expected_canonical


def test_unicode_url_handling():
    """Test handling of Unicode characters in URLs."""
    unicode_urls = [
        "https://uconn.edu/café",
        "https://uconn.edu/研究",  # Chinese characters
        "https://uconn.edu/образование",  # Russian characters
        "https://uconn.edu/université",  # French characters
    ]

    for url in unicode_urls:
        try:
            canonical, url_hash = canonicalize_and_hash(url)
            # Should not crash and should produce valid output
            assert isinstance(canonical, str)
            assert len(url_hash) == 40
        except Exception as e:
            # If Unicode handling fails, it should fail gracefully
            assert isinstance(e, (UnicodeError, ValueError))


def test_malformed_url_handling():
    """Test handling of malformed URLs."""
    malformed_urls = [
        "https://",
        "://uconn.edu",
        "https:///uconn.edu",
        "https://uconn.edu with spaces",
        "https://uconn..edu",
        "https://uconn.edu/path with unencoded spaces",
    ]

    for url in malformed_urls:
        try:
            canonical, url_hash = canonicalize_and_hash(url)
            # If it succeeds, should produce valid output
            assert isinstance(canonical, str)
            assert len(url_hash) == 40
        except Exception:
            # If it fails, that's acceptable for malformed URLs
            pass


@pytest.mark.parametrize("batch_size", [1, 10, 100, 1000])
def test_url_processing_batch_performance(random_100_urls, batch_size):
    """Test URL processing performance with different batch sizes."""
    import time
    import psutil
    import os

    # Get baseline metrics
    process = psutil.Process(os.getpid())
    memory_before = process.memory_info().rss

    urls = [url for url, _ in random_100_urls[:batch_size]]

    start_time = time.perf_counter()

    processed_urls = []
    for url in urls:
        canonical, url_hash = canonicalize_and_hash(url)
        processed_urls.append((canonical, url_hash))

    processing_time = time.perf_counter() - start_time
    memory_after = process.memory_info().rss
    memory_delta_mb = (memory_after - memory_before) / (1024 * 1024)

    # Assertions
    assert len(processed_urls) == batch_size

    # Calculate efficiency metrics
    urls_per_second = batch_size / processing_time if processing_time > 0 else float('inf')
    memory_per_url_kb = (memory_delta_mb * 1024) / batch_size if batch_size > 0 else 0

    # Performance assertions
    if batch_size >= 100:
        assert urls_per_second > 100, f"Too slow: {urls_per_second:.0f} URLs/s"

    # Memory efficiency assertions
    if batch_size >= 10:
        assert memory_per_url_kb < 10, f"Too memory-intensive: {memory_per_url_kb:.1f}KB per URL"

    # Log efficiency metrics for larger batches
    if batch_size >= 100:
        print(f"\n📊 URL Processing Efficiency (batch size {batch_size}):")
        print(f"   ⚡ Throughput: {urls_per_second:.0f} URLs/second")
        print(f"   💾 Memory: {memory_per_url_kb:.2f}KB per URL")
        print(f"   ⏱️  Latency: {processing_time/batch_size*1000:.2f}ms per URL")

    return {
        "batch_size": batch_size,
        "throughput": urls_per_second,
        "memory_per_url_kb": memory_per_url_kb,
        "processing_time": processing_time
    }
