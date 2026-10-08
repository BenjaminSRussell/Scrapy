"""IDN / unicode URL normalization (#207)."""
from __future__ import annotations

import pytest

from src.stage1.processors.url_processor import URLProcessor
from src.utils.url_canon import canonicalize_url, url_hash


@pytest.mark.parametrize(
    "a, b",
    [
        ("https://bücher.de/katalog", "https://xn--bcher-kva.de/katalog"),
        ("https://BÜCHER.DE/katalog", "https://xn--bcher-kva.de/katalog"),
        ("https://bücher.de./katalog", "https://xn--bcher-kva.de/katalog"),
        ("https://ｅｘａｍｐｌｅ.com/page", "https://example.com/page"),  # fullwidth -> NFKC
        ("https://例え.jp/パス", "https://xn--r8jz45g.jp/%E3%83%91%E3%82%B9"),
        ("https://example.com/caf\u00e9", "https://example.com/cafe\u0301"),  # NFC vs NFD path
        ("https://example.com/%7Euser", "https://example.com/~user"),
    ],
)
def test_equivalent_unicode_urls_dedupe(a, b):
    assert canonicalize_url(a) == canonicalize_url(b)
    assert url_hash(a) == url_hash(b)


def test_idn_host_collapses_to_punycode():
    assert canonicalize_url("https://Bücher.de/") == "https://xn--bcher-kva.de/"
    out = canonicalize_url("https://user:pw@例え.jp:8443/x")
    assert out == "https://user:pw@xn--r8jz45g.jp:8443/x"
    assert out.isascii()


def test_overlong_utf8_escape_is_not_decoded_into_slash():
    out = canonicalize_url("https://example.com/a/%C0%AF../etc")
    assert "%c0%af" in out
    assert "/a/../etc" not in out and "/a//" not in out


def test_reserved_escapes_kept():
    assert canonicalize_url("https://example.com/a%2Fb") == "https://example.com/a%2fb"


def test_ascii_urls_unchanged_by_207():
    # Same canonical form (and url_hash) as before IDN support.
    assert canonicalize_url("HTTPS://Example.com:443/Path/?utm_source=x&b=2&a=1#f") == "https://example.com/path?a=1&b=2"
    assert canonicalize_url("http://[::1]:80/x") == "http://[::1]/x"


def test_undecodable_host_rejected():
    assert canonicalize_url("https://" + "\u00e4" * 70 + ".com/") is None  # label > 63 octets


def test_idempotent():
    for u in ["https://bücher.de/Straße?q=ü", "https://例え.jp/パス/", "https://example.com/%7e"]:
        once = canonicalize_url(u)
        assert canonicalize_url(once) == once


def test_url_processor_uses_idn_rules():
    proc = URLProcessor.__new__(URLProcessor)
    assert proc.normalize_url("https://bücher.de/x") == "https://xn--bcher-kva.de/x"
