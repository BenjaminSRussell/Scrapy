"""src/utils/validation.py: URL/payload validators with stable rejection codes (#265)."""

from __future__ import annotations

import pytest

from src.utils import validation as v

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "url",
    [
        "https://uconn.edu",
        "http://uconn.edu/",
        "https://www.cs.uconn.edu/a/b?x=1#frag",
        "https://uconn.edu:8443/path",
        "http://127.0.0.1:8080/health",
        "https://[2001:db8::1]/x",
        "https://xn--bcher-kva.example/",  # IDN in punycode
        "https://user:pw@uconn.edu/",
    ],
)
def test_valid_urls(url):
    assert v.url_rejection_reason(url) is None
    assert v.is_valid_url(url) is True


@pytest.mark.parametrize(
    "url,code",
    [
        (None, v.URL_NOT_A_STRING),
        (b"https://uconn.edu", v.URL_NOT_A_STRING),
        (42, v.URL_NOT_A_STRING),
        ("", v.URL_EMPTY),
        ("   ", v.URL_EMPTY),
        ("https://uconn.edu/" + "a" * 2048, v.URL_TOO_LONG),
        ("https://uconn.edu/a b", v.URL_WHITESPACE),
        ("https://uconn.edu/\nSet-Cookie:x", v.URL_WHITESPACE),
        (" https://uconn.edu/", v.URL_WHITESPACE),
        ("https://[::1/", v.URL_UNPARSABLE),
        ("https://uconn.edu:99999/", v.URL_UNPARSABLE),
        ("https://uconn.edu:http/", v.URL_UNPARSABLE),
        ("ftp://uconn.edu/file", v.URL_BAD_SCHEME),
        ("javascript:alert(1)", v.URL_BAD_SCHEME),
        ("mailto:a@uconn.edu", v.URL_BAD_SCHEME),
        ("uconn.edu/page", v.URL_BAD_SCHEME),
        ("//uconn.edu/page", v.URL_BAD_SCHEME),
        ("https://", v.URL_NO_HOST),
        ("http://:80/x", v.URL_NO_HOST),
        ("https:///path", v.URL_NO_HOST),
    ],
)
def test_invalid_urls_have_stable_codes(url, code):
    assert v.url_rejection_reason(url) == code
    assert v.is_valid_url(url) is False


def test_codes_are_a_closed_stable_set():
    assert v.URL_REJECTION_CODES == {
        "not_a_string", "empty", "too_long", "unparsable", "bad_scheme", "no_host", "whitespace",
    }
    assert v.MAX_URL_LENGTH == 2048
    assert v.is_valid_url("https://u.edu/" + "a" * (2047 - len("https://u.edu/")))  # 2047 chars


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://uconn.edu/", True),
        ("https://www.cs.UCONN.edu/x", True),
        ("https://uconn.edu./x", True),
        ("https://uconn.edu.evil.com/", False),
        ("https://notuconn.edu/", False),
        ("https://uconn.edu@evil.com/", False),  # userinfo trick
        ("https://evil.com/?next=uconn.edu", False),
        ("ftp://uconn.edu/", False),
    ],
)
def test_is_uconn_domain_matches_host_exactly_or_as_subdomain(url, expected):
    assert v.is_uconn_domain(url) is expected


@pytest.mark.parametrize(
    "text,max_length,expected",
    [
        ("  Hello   World  ", None, "Hello World"),
        ("a\tb\nc\r\nd", None, "a b c d"),
        ("abcdef", 3, "abc"),
        ("abc", 0, ""),  # 0 means empty, not "no limit" (#757)
        ("", 10, ""),
        (None, None, ""),
        (123, None, ""),
    ],
)
def test_sanitize_text(text, max_length, expected):
    assert v.sanitize_text(text, max_length=max_length) == expected


@pytest.mark.parametrize(
    "data,required,expected",
    [
        ({"url": "u", "title": "t"}, ["url", "title"], True),
        ({"url": "u"}, ["url", "title"], False),
        ({"url": None}, ["url"], True),  # presence check, not truthiness
        ({}, [], True),
        (["url"], ["url"], False),
        (None, ["url"], False),
    ],
)
def test_validate_stage_data(data, required, expected):
    assert v.validate_stage_data(data, required) is expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("report.pdf", True),
        ("a_b-c.1.txt", True),
        ("../etc/passwd", False),
        ("a/b", False),
        ("a\\b", False),
        ("..", False),
        ("x" * 256, False),
        ("x" * 255, True),
        ("na me.txt", False),
        ("", False),
        (None, False),
    ],
)
def test_is_safe_filename(name, expected):
    assert v.is_safe_filename(name) is expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://UConn.EDU/Page/?utm_source=email#section", "https://uconn.edu/page"),
        ("https://uconn.edu/a/?id=7&utm_medium=x", "https://uconn.edu/a?id=7"),
        ("not a url", "not a url"),  # invalid input returned unchanged
    ],
)
def test_normalize_url(url, expected):
    assert v.normalize_url(url) == expected


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.uconn.edu/page", "www.uconn.edu"),
        ("https://UCONN.edu:8443/", "uconn.edu:8443"),
        ("ftp://uconn.edu/", ""),
        ("", ""),
    ],
)
def test_extract_domain(url, expected):
    assert v.extract_domain(url) == expected
