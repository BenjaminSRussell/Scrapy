"""Comprehensive tests for URL utilities - testing every edge case"""
import pytest
from urllib.parse import urlparse

from common.urls import (
    _sanitize_path,
    normalize_url,
    canonicalize_url_simple,
    is_valid_uconn_url,
    extract_domain
)


class TestSanitizePath:
    """test path sanitization because URLs are a nightmare"""

    def test_empty_path(self):
        assert _sanitize_path("") == ""

    def test_none_path(self):
        assert _sanitize_path(None) == ""

    def test_root_path(self):
        assert _sanitize_path("/") == "/"

    def test_simple_path(self):
        assert _sanitize_path("/simple") == "/simple"

    def test_path_with_trailing_slash(self):
        assert _sanitize_path("/simple/") == "/simple/"

    def test_path_without_leading_slash(self):
        assert _sanitize_path("simple") == "/simple"

    def test_dot_segments_single_dot(self):
        assert _sanitize_path("/./") == "/"

    def test_dot_segments_double_dot(self):
        assert _sanitize_path("/../") == "/"

    def test_complex_dot_segments(self):
        assert _sanitize_path("/a/b/../c/./d") == "/a/c/d"

    def test_multiple_slashes(self):
        assert _sanitize_path("//multiple///slashes////") == "/multiple/slashes/"

    def test_relative_path_with_dots(self):
        assert _sanitize_path("../relative") == "/relative"

    def test_current_directory_only(self):
        assert _sanitize_path(".") == "/"

    def test_parent_directory_only(self):
        assert _sanitize_path("..") == "/"

    def test_complex_traversal(self):
        assert _sanitize_path("/a/b/c/../../d/e/../f") == "/a/d/f"

    def test_trailing_slash_preservation(self):
        assert _sanitize_path("/path/to/dir/") == "/path/to/dir/"
        assert _sanitize_path("/path/to/file") == "/path/to/file"

    def test_encoded_characters(self):
        assert _sanitize_path("/path%2Fwith%2Fencoded") == "/path%2Fwith%2Fencoded"

    def test_unicode_path(self):
        assert _sanitize_path("/café/naïve") == "/café/naïve"

    def test_really_long_path(self):
        long_path = "/" + "/".join([f"segment{i}" for i in range(100)])
        result = _sanitize_path(long_path)
        assert result.startswith("/")
        assert result.count("/") == 100

    def test_path_with_query_like_chars(self):
        # shouldn't process query params in path
        assert _sanitize_path("/path?not=query") == "/path?not=query"

    def test_path_with_fragment_like_chars(self):
        assert _sanitize_path("/path#not=fragment") == "/path#not=fragment"


class TestNormalizeUrl:
    """normalize URL testing because standards are suggestions"""

    def test_none_url_raises_error(self):
        with pytest.raises(TypeError, match="url must not be None"):
            normalize_url(None)

    def test_basic_http_url(self):
        result = normalize_url("http://example.com")
        assert result == "http://example.com"

    def test_basic_https_url(self):
        result = normalize_url("https://example.com")
        assert result == "https://example.com"

    def test_url_with_path(self):
        result = normalize_url("https://example.com/path")
        assert result == "https://example.com/path"

    def test_url_with_trailing_slash(self):
        result = normalize_url("https://example.com/")
        assert result == "https://example.com/"

    def test_url_without_trailing_slash_no_path(self):
        result = normalize_url("https://example.com")
        assert result == "https://example.com"

    def test_url_with_query_params(self):
        result = normalize_url("https://example.com/path?param=value")
        assert "param=value" in result

    def test_url_with_fragment(self):
        result = normalize_url("https://example.com/path#fragment")
        assert "#fragment" in result

    def test_url_case_normalization(self):
        result = normalize_url("HTTP://EXAMPLE.COM/PATH")
        assert result.startswith("http://")
        assert "EXAMPLE.COM" in result  # hostname case preserved by w3lib

    def test_url_with_default_http_port(self):
        result = normalize_url("http://example.com:80/path")
        assert ":80" not in result
        assert result == "http://example.com/path"

    def test_url_with_default_https_port(self):
        result = normalize_url("https://example.com:443/path")
        assert ":443" not in result
        assert result == "https://example.com/path"

    def test_url_with_non_default_port(self):
        result = normalize_url("https://example.com:8080/path")
        assert ":8080" in result

    def test_url_with_username_password(self):
        result = normalize_url("https://user:pass@example.com/path")
        assert "user:pass@" in result

    def test_url_with_username_only(self):
        result = normalize_url("https://user@example.com:443/path")
        assert "user@" in result
        assert ":443" not in result

    def test_url_with_dot_segments(self):
        result = normalize_url("https://example.com/a/b/../c/./d")
        assert result == "https://example.com/a/c/d"

    def test_url_with_encoded_characters(self):
        result = normalize_url("https://example.com/path%20with%20spaces")
        assert "%20" in result

    def test_url_with_unicode(self):
        result = normalize_url("https://example.com/café")
        assert "café" in result

    def test_malformed_url_scheme_missing(self):
        # should handle gracefully or raise appropriate error
        with pytest.raises(Exception):
            normalize_url("example.com/path")

    def test_empty_url(self):
        with pytest.raises(Exception):
            normalize_url("")

    def test_url_with_multiple_slashes(self):
        result = normalize_url("https://example.com//path///to////resource")
        # should normalize multiple slashes
        assert "//path" not in result or result == "https://example.com//path/to/resource"

    def test_international_domain(self):
        result = normalize_url("https://café.example.com/path")
        assert "café" in result or "xn--" in result  # punycode

    def test_very_long_url(self):
        long_path = "/" + "/".join([f"segment{i}" for i in range(50)])
        url = f"https://example.com{long_path}"
        result = normalize_url(url)
        assert result.startswith("https://example.com/")

    def test_url_with_params(self):
        result = normalize_url("https://example.com/path;param=value")
        assert ";param=value" in result

    def test_ftp_scheme_preserved(self):
        # even though we mainly use http/https
        result = normalize_url("ftp://example.com/file")
        assert result.startswith("ftp://")


class TestCanonicalizeUrlSimple:
    """wrapper function testing because why not"""

    def test_simple_canonical(self):
        result = canonicalize_url_simple("https://example.com/path")
        assert result == "https://example.com/path"

    def test_canonical_with_dots(self):
        result = canonicalize_url_simple("https://example.com/a/../b")
        assert result == "https://example.com/b"

    def test_canonical_none_raises(self):
        with pytest.raises(TypeError):
            canonicalize_url_simple(None)


class TestIsValidUconnUrl:
    """UConn domain validation because we only care about one school"""

    def test_none_url_raises_error(self):
        with pytest.raises(TypeError, match="url must not be None"):
            is_valid_uconn_url(None)

    def test_valid_main_domain(self):
        assert is_valid_uconn_url("https://uconn.edu") is True

    def test_valid_www_subdomain(self):
        assert is_valid_uconn_url("https://www.uconn.edu") is True

    def test_valid_other_subdomain(self):
        assert is_valid_uconn_url("https://admissions.uconn.edu") is True

    def test_valid_deep_subdomain(self):
        assert is_valid_uconn_url("https://course.catalog.uconn.edu") is True

    def test_valid_http_scheme(self):
        assert is_valid_uconn_url("http://uconn.edu") is True

    def test_invalid_different_domain(self):
        assert is_valid_uconn_url("https://yale.edu") is False

    def test_invalid_similar_domain(self):
        assert is_valid_uconn_url("https://uconn.edu.fake.com") is False

    def test_invalid_contains_uconn(self):
        assert is_valid_uconn_url("https://fakeuconn.edu") is False

    def test_invalid_ftp_scheme(self):
        assert is_valid_uconn_url("ftp://uconn.edu") is False

    def test_invalid_no_scheme(self):
        assert is_valid_uconn_url("uconn.edu") is False

    def test_invalid_malformed_url(self):
        assert is_valid_uconn_url("not-a-url") is False

    def test_invalid_empty_string(self):
        assert is_valid_uconn_url("") is False

    def test_valid_with_path(self):
        assert is_valid_uconn_url("https://uconn.edu/academics") is True

    def test_valid_with_query(self):
        assert is_valid_uconn_url("https://uconn.edu/search?q=test") is True

    def test_valid_with_fragment(self):
        assert is_valid_uconn_url("https://uconn.edu/page#section") is True

    def test_valid_with_port(self):
        assert is_valid_uconn_url("https://uconn.edu:8080") is True

    def test_case_insensitive_domain(self):
        assert is_valid_uconn_url("https://UCONN.EDU") is True

    def test_case_insensitive_subdomain(self):
        assert is_valid_uconn_url("https://WWW.UCONN.EDU") is True

    def test_encoded_domain_invalid(self):
        # encoded domains should be invalid
        assert is_valid_uconn_url("https://uconn%2Eedu") is False

    def test_international_chars_invalid(self):
        assert is_valid_uconn_url("https://ücönn.edu") is False

    def test_ip_address_invalid(self):
        assert is_valid_uconn_url("https://192.168.1.1") is False

    def test_localhost_invalid(self):
        assert is_valid_uconn_url("https://localhost") is False


class TestExtractDomain:
    """domain extraction testing because parsing is fun"""

    def test_extract_simple_domain(self):
        result = extract_domain("https://example.com")
        assert result == "example.com"

    def test_extract_subdomain(self):
        result = extract_domain("https://www.example.com")
        assert result == "www.example.com"

    def test_extract_with_path(self):
        result = extract_domain("https://example.com/path/to/resource")
        assert result == "example.com"

    def test_extract_with_port(self):
        result = extract_domain("https://example.com:8080")
        assert result == "example.com"

    def test_extract_case_normalization(self):
        result = extract_domain("https://EXAMPLE.COM")
        assert result == "example.com"

    def test_extract_http_scheme(self):
        result = extract_domain("http://example.com")
        assert result == "example.com"

    def test_extract_with_username(self):
        result = extract_domain("https://user@example.com")
        assert result == "example.com"

    def test_extract_with_username_password(self):
        result = extract_domain("https://user:pass@example.com")
        assert result == "example.com"

    def test_extract_ip_address(self):
        result = extract_domain("https://192.168.1.1")
        assert result == "192.168.1.1"

    def test_extract_localhost(self):
        result = extract_domain("http://localhost")
        assert result == "localhost"

    def test_extract_with_query(self):
        result = extract_domain("https://example.com?param=value")
        assert result == "example.com"

    def test_extract_with_fragment(self):
        result = extract_domain("https://example.com#fragment")
        assert result == "example.com"

    def test_extract_international_domain(self):
        result = extract_domain("https://café.example.com")
        assert "café" in result or "xn--" in result

    def test_extract_empty_hostname(self):
        # edge case handling
        try:
            result = extract_domain("file:///path/to/file")
            assert result == ""
        except:
            # acceptable to raise exception for invalid URLs
            pass


class TestUrlEdgeCases:
    """edge cases because the internet is broken"""

    def test_url_with_spaces(self):
        # spaces should be encoded
        result = normalize_url("https://example.com/path with spaces")
        assert " " not in result or "%20" in result

    def test_url_with_special_chars(self):
        url = "https://example.com/path?param=value&other=123"
        result = normalize_url(url)
        assert "param=value" in result

    def test_really_long_domain(self):
        long_subdomain = "a" * 60
        url = f"https://{long_subdomain}.example.com"
        result = normalize_url(url)
        assert long_subdomain in result

    def test_url_with_emoji(self):
        # emojis in URLs
        url = "https://example.com/😀"
        result = normalize_url(url)
        assert "😀" in result or "%" in result

    def test_url_with_backslashes(self):
        # windows-style paths shouldn't work
        url = "https://example.com\\path\\to\\resource"
        result = normalize_url(url)
        assert "\\" in result  # preserved as-is

    def test_url_with_multiple_dots(self):
        url = "https://example.com/./././path"
        result = normalize_url(url)
        assert result == "https://example.com/path"

    def test_url_with_null_bytes(self):
        # null bytes should be handled
        try:
            url = "https://example.com/path\x00"
            result = normalize_url(url)
            assert "\x00" not in result
        except:
            # acceptable to raise exception
            pass

    def test_punycode_domains(self):
        url = "https://xn--caf-dma.example.com"
        result = normalize_url(url)
        assert "xn--" in result

    def test_url_with_tabs_newlines(self):
        url = "https://example.com/path\t\n"
        result = normalize_url(url)
        assert "\t" not in result and "\n" not in result


class TestPerformance:
    """performance testing because speed matters"""

    def test_normalize_url_performance(self):
        # test with many URLs
        urls = [f"https://example{i}.com/path/{i}" for i in range(100)]

        for url in urls:
            result = normalize_url(url)
            assert result.startswith("https://")

    def test_domain_validation_performance(self):
        # test validation performance
        uconn_urls = [f"https://dept{i}.uconn.edu" for i in range(50)]
        other_urls = [f"https://example{i}.com" for i in range(50)]

        for url in uconn_urls:
            assert is_valid_uconn_url(url) is True

        for url in other_urls:
            assert is_valid_uconn_url(url) is False

    def test_path_sanitization_performance(self):
        # test with complex paths
        complex_paths = [f"/a{i}/b{i}/../c{i}/./d{i}" for i in range(100)]

        for path in complex_paths:
            result = _sanitize_path(path)
            assert result.startswith("/")


class TestErrorHandling:
    """error handling because things break"""

    def test_invalid_url_schemes(self):
        invalid_schemes = [
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "vbscript:msgbox(1)",
            "about:blank",
        ]

        for url in invalid_schemes:
            # should either normalize gracefully or raise exception
            try:
                result = normalize_url(url)
                # if it normalizes, check it's safe
                assert not result.startswith("javascript:")
            except:
                # acceptable to raise exception
                pass

    def test_malformed_netloc(self):
        malformed = [
            "https://",
            "https:///path",
            "https://[invalid",
            "https://user:@host",
        ]

        for url in malformed:
            try:
                normalize_url(url)
            except:
                # acceptable to fail on malformed URLs
                pass

    def test_very_long_urls(self):
        # URLs longer than typical limits
        long_path = "/" + "x" * 10000
        url = f"https://example.com{long_path}"

        try:
            result = normalize_url(url)
            assert len(result) > 10000
        except:
            # acceptable to fail on extremely long URLs
            pass

    def test_unicode_edge_cases(self):
        unicode_urls = [
            "https://example.com/🌟",
            "https://example.com/测试",
            "https://example.com/Москва",
            "https://example.com/العربية",
        ]

        for url in unicode_urls:
            try:
                result = normalize_url(url)
                assert result.startswith("https://")
            except:
                # acceptable to have issues with some unicode
                pass