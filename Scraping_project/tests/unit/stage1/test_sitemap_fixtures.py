"""#234: SitemapParser against on-disk fixture sitemaps (offline, httpx.MockTransport).

Covers urlset, sitemapindex (absolute + relative children), gzipped files,
legacy no-namespace sitemaps, plain-text sitemaps, malformed XML, an HTML error
page and XML entity attacks. See tests/fixtures/sitemaps/README.md.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from src.stage1.sitemap_parser import SitemapParser

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sitemaps"
BASE = "https://www.example.edu"

ROUTES = {
    "/sitemap.xml": ("index.xml", "application/xml"),
    "/sitemap-pages.xml": ("pages.xml", "application/xml"),
    "/sitemap-news.xml.gz": ("news.xml.gz", "application/x-gzip"),
    "/sitemap-broken.xml": ("broken.xml", "application/xml"),
    "/legacy.xml": ("no_namespace.xml", "text/xml"),
    "/laughs.xml": ("billion_laughs.xml", "application/xml"),
    "/xxe.xml": ("xxe.xml", "application/xml"),
    "/sitemap.txt": ("plain.txt", "text/plain; charset=utf-8"),
    "/error.xml": ("error_page.html", "text/html"),
}


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def handler(request: httpx.Request) -> httpx.Response:
    route = ROUTES.get(request.url.path)
    if route is None:
        return httpx.Response(404)
    name, ctype = route
    return httpx.Response(200, content=fixture(name), headers={"content-type": ctype})


def walk(path: str, **limits) -> SitemapParser:
    parser = SitemapParser(BASE, **limits)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await parser._parse_sitemap_recursive(client, BASE + path, depth=0)

    asyncio.run(go())
    return parser


def test_all_fixtures_exist():
    for name, _ in ROUTES.values():
        assert (FIXTURES / name).is_file(), name


def test_urlset_extracts_stripped_unique_locs_and_skips_empty_ones():
    parser = walk("/sitemap-pages.xml")
    assert sorted(parser.discovered_urls) == [
        "https://www.example.edu/academics",
        "https://www.example.edu/admissions",
        "https://www.example.edu/research",
    ]
    assert "https://www.example.edu/img/campus.jpg" not in parser.discovered_urls  # image ext is not a page
    assert parser.stats["urlsets"] == 1


def test_non_numeric_priority_does_not_drop_the_urlset():
    # Regression: float("high") raised and the whole urlset was discarded.
    parser = walk("/sitemap-pages.xml")
    assert "https://www.example.edu/research" in parser.discovered_urls
    assert len(parser.discovered_urls) == 3


def test_index_walks_absolute_relative_and_gzipped_children_and_survives_a_broken_one():
    parser = walk("/sitemap.xml")
    assert parser.stats["indexes"] == 1
    assert parser.stats["urlsets"] == 2  # pages + news; broken.xml is not counted
    assert parser.visited_sitemaps == {
        BASE + "/sitemap.xml",
        BASE + "/sitemap-pages.xml",
        BASE + "/sitemap-news.xml.gz",
        BASE + "/sitemap-broken.xml",
    }
    assert "https://www.example.edu/news/2026/fall-open-house" in parser.discovered_urls
    assert "https://www.example.edu/admissions" in parser.discovered_urls
    assert not any("truncated" in u or "never-closed" in u for u in parser.discovered_urls)


def test_malformed_xml_yields_nothing_and_does_not_raise(caplog):
    parser = walk("/sitemap-broken.xml")
    assert parser.discovered_urls == set()
    assert any("Failed to parse sitemap XML" in r.getMessage() for r in caplog.records)


def test_legacy_no_namespace_sitemap():
    assert walk("/legacy.xml").discovered_urls == {
        "https://www.example.edu/legacy-a",
        "https://www.example.edu/legacy-b",
    }


def test_plain_text_sitemap_keeps_only_http_lines():
    assert walk("/sitemap.txt").discovered_urls == {
        "https://www.example.edu/plain-a",
        "https://www.example.edu/plain-b",
    }


def test_html_error_page_served_as_sitemap_yields_nothing():
    assert walk("/error.xml").discovered_urls == set()


@pytest.mark.parametrize("path", ["/laughs.xml", "/xxe.xml"])
def test_entity_attacks_are_rejected(path, caplog):
    parser = walk(path)
    assert parser.discovered_urls == set()
    assert any("Rejected unsafe XML" in r.getMessage() for r in caplog.records)


def test_byte_cap_applies_to_fixture_files():
    parser = walk("/sitemap-pages.xml", max_bytes=100)
    assert parser.discovered_urls == set()
    assert "bytes" in parser.limits_hit


def test_url_cap_keeps_document_order():
    parser = walk("/sitemap-pages.xml", max_urls=1)
    assert parser.discovered_urls == {"https://www.example.edu/admissions"}


@pytest.mark.parametrize(
    "text, expected",
    [(None, None), ("0.8", 0.8), (" 1.0 ", 1.0), ("0", 0.0), ("high", None), ("", None), ("1.5", None), ("-1", None), ("nan", None)],
)
def test_parse_priority(text, expected):
    assert SitemapParser._parse_priority(text) == expected
