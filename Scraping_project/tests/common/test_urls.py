import pytest

from src.common.urls import normalize_url


@pytest.mark.parametrize(
    "url, expected",
    [
        # Existing test cases from memory
        ("http://example.com", "http://example.com"),
        ("https://example.com/a/b/../c", "https://example.com/a/c"),
        ("http://example.com/a//b", "http://example.com/a//b"),
    ],
)
def test_normalize_url(url, expected):
    assert normalize_url(url) == expected