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
    pytest.param(
        "https://fake-uconn.edu",
        False,
        marks=pytest.mark.xfail(
            reason="is_valid_uconn_url currently accepts fake hyphenated domains",
            strict=True,
        ),
    ),
    ("https://google.com", False),
    ("https://uconn.com", False),
    ("https://example.edu/uconn.com", False),
    pytest.param(
        "ftp://uconn.edu/resource",
        False,
        marks=pytest.mark.xfail(
            reason="is_valid_uconn_url treats non-HTTP schemes as valid",
            strict=True,
        ),
    ),
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
