import pytest
from src.common.urls import normalize_url, is_valid_uconn_url

def test_normalize_url_basic():
    assert normalize_url("http://example.com/path") == "http://example.com/path"
    assert normalize_url("https://www.Example.com/path/") == "https://www.example.com/path/"

def test_normalize_url_fragments():
    assert normalize_url("http://example.com/path#fragment") == "http://example.com/path"
    assert normalize_url("http://example.com/path?a=1#frag") == "http://example.com/path?a=1"

def test_normalize_url_query_sorting():
    assert normalize_url("http://example.com/path?b=2&a=1") == "http://example.com/path?a=1&b=2"
    assert normalize_url("http://example.com/path?c=3&a=1&b=2") == "http://example.com/path?a=1&b=2&c=3"

def test_normalize_url_case_sensitivity():
    assert normalize_url("http://example.com/Path/TO/File?q=Search") == "http://example.com/path/to/file?q=search"
    assert normalize_url("http://example.com/Path/TO/File?q=Search", lowercase_path_query=False) == "http://example.com/Path/TO/File?q=Search"

def test_normalize_url_dot_segments():
    assert normalize_url("http://example.com/a/b/../c") == "http://example.com/a/c"
    assert normalize_url("http://example.com/a/./b/") == "http://example.com/a/b/"
    assert normalize_url("http://example.com/a/../") == "http://example.com/"

def test_is_valid_uconn_url():
    assert is_valid_uconn_url("http://uconn.edu") is True
    assert is_valid_uconn_url("https://www.uconn.edu") is True
    assert is_valid_uconn_url("http://subdomain.uconn.edu") is True
    assert is_valid_uconn_url("https://www.subdomain.uconn.edu/path") is True
    assert is_valid_uconn_url("http://example.com") is False
    assert is_valid_uconn_url("ftp://uconn.edu") is False
    assert is_valid_uconn_url("http://uconn.edu.fake.com") is False
