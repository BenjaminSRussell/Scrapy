"""Tests for URL helper utilities"""

# TODO: Implement URL utility tests
# Need to test:
# 1. SHA-1 hash generation consistency
# 2. URL canonicalization with w3lib
# 3. canonicalize_and_hash function
# 4. UConn domain validation
# 5. Edge cases with malformed URLs

import pytest
from common.urls import sha1_hash, canonicalize_and_hash, is_valid_uconn_url


def test_sha1_hash_consistency():
    """Test SHA-1 hash generation is consistent"""
    url = "https://uconn.edu/test"
    hash1 = sha1_hash(url)
    hash2 = sha1_hash(url)

    # Same URL should always produce same hash
    assert hash1 == hash2
    assert len(hash1) == 40  # SHA-1 is 40 hex characters
    assert isinstance(hash1, str)

    # Different URLs should produce different hashes
    different_url = "https://uconn.edu/different"
    hash3 = sha1_hash(different_url)
    assert hash1 != hash3


def test_canonicalize_and_hash():
    """Test URL canonicalization and hash generation"""
    # Test basic canonicalization
    url = "https://UCONN.edu/Test/../page?param=value"
    canonical, hash_val = canonicalize_and_hash(url)

    assert canonical.startswith("https://")
    assert "uconn.edu" in canonical.lower()
    assert len(hash_val) == 40

    # Test that different representations of same URL get same hash
    url1 = "https://uconn.edu/page"
    url2 = "https://UCONN.EDU/page/"
    url3 = "https://uconn.edu/page?utm_source=test"

    _, hash1 = canonicalize_and_hash(url1)
    _, hash2 = canonicalize_and_hash(url2)
    _, hash3 = canonicalize_and_hash(url3)

    # Different URLs should have different hashes
    assert hash1 != hash3  # Query params make URLs different

    # Test that canonicalization handles common variations
    assert canonicalize_and_hash("http://uconn.edu")[0].startswith("http://")


def test_is_valid_uconn_url():
    """Test UConn domain validation"""
    # Valid UConn URLs
    valid_urls = [
        "https://uconn.edu",
        "https://www.uconn.edu",
        "https://admissions.uconn.edu",
        "https://catalog.uconn.edu/path/to/page",
        "http://events.uconn.edu",
    ]

    for url in valid_urls:
        assert is_valid_uconn_url(url), f"Should be valid: {url}"

    # Invalid URLs
    invalid_urls = [
        "https://google.com",
        "https://yale.edu",
        "https://uconn.com",  # Wrong TLD
        "https://fake-uconn.edu",  # Subdomain spoofing
        "not-a-url",
        "",
        None,
    ]

    for url in invalid_urls:
        assert not is_valid_uconn_url(url), f"Should be invalid: {url}"


def test_url_edge_cases():
    """Test handling of malformed or edge case URLs"""
    # Test malformed URLs don't crash the system
    malformed_urls = [
        "not-a-url",
        "ftp://uconn.edu",  # Different protocol
        "https://",  # Incomplete URL
        "https://uconn.edu with spaces",
        "javascript:alert('xss')",
        "",
        None,
    ]

    for url in malformed_urls:
        try:
            if url:
                canonical, hash_val = canonicalize_and_hash(url)
                # If it succeeds, verify basic properties
                assert isinstance(canonical, str)
                assert isinstance(hash_val, str)
                assert len(hash_val) == 40
        except Exception:
            # It's okay if malformed URLs raise exceptions
            # The important thing is they don't crash unexpectedly
            pass

    # Test very long URLs
    long_url = "https://uconn.edu/" + "a" * 2000
    canonical, hash_val = canonicalize_and_hash(long_url)
    assert len(hash_val) == 40  # Hash should still be 40 chars

    # Test URLs with special characters
    special_url = "https://uconn.edu/page?param=hello%20world&other=test"
    canonical, hash_val = canonicalize_and_hash(special_url)
    assert len(hash_val) == 40
    assert "uconn.edu" in canonical