"""#251: public-suffix-aware partition domain."""

import socket

import pytest

from src.utils.validation import extract_domain, registrable_domain


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://news.bbc.co.uk/article", "bbc.co.uk"),       # multi-part suffix
        ("https://www.ox.ac.uk/", "ox.ac.uk"),
        ("https://www.cs.uconn.edu/people", "uconn.edu"),       # multi-label edu host
        ("https://catalog.uconn.edu", "uconn.edu"),
        ("https://UConn.EDU:8443/x", "uconn.edu"),              # case + port
        ("https://example.com.au/", "example.com.au"),
        ("https://foo.github.io/page", "foo.github.io"),        # private PSL suffix
        ("http://192.168.1.10/status", "192.168.1.10"),         # IPv4
        ("http://[2001:db8::1]:8080/", "2001:db8::1"),          # IPv6
        ("http://localhost:8000/", "localhost"),
        ("www.uconn.edu", "uconn.edu"),                         # bare host
        ("", "unknown"),
        ("https:///nohost", "unknown"),
    ],
)
def test_registrable_domain(value, expected):
    assert registrable_domain(value) == expected


def test_differs_from_extract_domain_by_design():
    url = "https://www.cs.uconn.edu/people"
    assert extract_domain(url) == "www.cs.uconn.edu"
    assert registrable_domain(url) == "uconn.edu"


def test_offline_no_network(monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("public suffix lookup must not touch the network")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    assert registrable_domain("https://shop.example.co.jp/") == "example.co.jp"


def test_lakehouse_partition_uses_registrable_domain(tmp_path):
    from src.lakehouse.lakehouse_manager import LakehouseManager

    mgr = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    rows = [{"url": "https://news.bbc.co.uk/a", "url_hash": "1"}, {"url": "https://www.cs.uconn.edu/b", "url_hash": "2"}]
    assert mgr.write("stage1_discovery", rows, async_write=False) is not False
    assert {r["domain"] for r in mgr.read("stage1_discovery")} == {"bbc.co.uk", "uconn.edu"}
