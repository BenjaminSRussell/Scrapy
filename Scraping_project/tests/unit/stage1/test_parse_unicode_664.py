"""Unicode and response-encoding edge cases in spider parse (#664).

Spiders live in ``src/stage1`` (there is no ``src/spiders``). ``ScoutSpider.parse``
is the production parser. ``BaseSpider.parse`` is the shared parser it is built
on. Both discover URLs through ``URLExtractor`` and ``canonicalize_url``.

Text and URL policy these tests pin (not the process locale):

* Bytes are decoded with the declared charset (Content-Type, else
  ``<meta charset>``). ``locale.getpreferredencoding`` is forced to ``cp037``
  for the whole module, so a locale fallback cannot satisfy the assertions.
* Undecodable bytes become U+FFFD. Parse does not raise.
* Selector text keeps the decoded code points. Body text is not NFC/NFKC
  normalized: a combining acute stays a combining acute. Punctuation, RTL
  text and ZWJ emoji round-trip.
* Dedup keys are ``url_hash`` of ``canonicalize_url``: NFKC (NFC, NFD and
  fullwidth paths match), IDNA punycode, UTF-8 percent-encoding, lower case.
  Invalid percent escapes are kept, not decoded into characters or ``/``.
  A ``decodeURIComponent`` argument that is not valid UTF-8 is dropped.

Fixtures are loaded with ``Path.read_bytes``. ``HtmlResponse`` is built
without ``encoding=`` so Scrapy discovers the charset from the fixture.
"""

from __future__ import annotations

import locale
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fakeredis
import pytest
import scrapy
from scrapy.http import HtmlResponse, Request

from src.stage1.base_spider import BaseSpider
from src.stage1.scout_spider import ScoutSpider
from src.utils.url_canon import url_hash

pytestmark = [pytest.mark.scrapy, pytest.mark.stage1]

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "unicode"
PAGE = "https://example.com/dept/"

# Title of utf8_mixed.html: naive-i, cafe, em dash, curly quotes, ellipsis, coffee+VS16.
UTF8_TITLE = "Na\u00efve caf\u00e9 \u2014 \u201cquotes\u201d \u2026 \u2615\ufe0f"
# windows1252_declared.html title. Em dash and euro are cp1252 bytes 0x97 and 0x80.
CP1252_TITLE = "Caf\u00e9 \u2014 na\u00efve \u201cquotes\u201d \u20ac"
CP1252_PARAGRAPH = "R\u00e9sum\u00e9: the caf\u00e9 costs 5\u20ac."

CAFE = "https://example.com/dept/caf%c3%a9"
RTL = "https://example.com/dept/%d9%85%d8%b1%d8%ad%d8%a8%d8%a7"
IDN = "https://xn--bcher-kva.example.com/pfad"
JSON_LD = "https://example.com/dept/json-%c3%a9"
CAFE_QUERY = "https://example.com/dept/caf%c3%a9?b=2"

UTF8_URLS = frozenset({CAFE, RTL, IDN, JSON_LD, CAFE_QUERY})
CP1252_URLS = frozenset({CAFE, "https://xn--bcher-kva.example.com/katalog"})
# %FF%FE and %ZZ are kept escaped (lowercased). The bad decodeURIComponent is absent.
MALFORMED_URLS = frozenset({
    "https://example.com/dept/ok",
    "https://example.com/dept/%ff%fe",
    "https://example.com/dept/%zz",
})

PARSERS = [ScoutSpider, BaseSpider]


@pytest.fixture(autouse=True)
def _locale_cannot_decode_pages(monkeypatch):
    """A cp037 locale must not change decoded titles, bodies or URLs."""
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=False: "cp037")


@pytest.fixture
def make_spider():
    def _make(cls):
        redis_helper = SimpleNamespace(client=fakeredis.FakeRedis())
        with patch("src.stage1.scout_spider.get_delta_manager"), \
                patch("src.stage1.experimental.base_spider.get_redis", return_value=redis_helper):
            spider = cls(allowed_domains=["example.com"])
        spider.expand_seeds = False  # no SeedManager writes
        return spider

    return _make


def _response(name: str, content_type: bytes) -> HtmlResponse:
    body = (FIXTURES / name).read_bytes()
    return HtmlResponse(
        url=PAGE,
        body=body,
        headers={b"Content-Type": content_type},
        request=Request(PAGE, meta={"depth": 0}),
    )


def _requests(spider, response) -> list[scrapy.Request]:
    parsed = spider.parse(response)
    outputs = parsed if isinstance(parsed, list) else list(parsed)
    return [item for item in outputs if isinstance(item, scrapy.Request)]


def test_fixture_bytes_match_their_declared_encodings():
    utf8 = (FIXTURES / "utf8_mixed.html").read_bytes()
    text = utf8.decode("utf-8")  # strict; fails if the file was transcoded
    assert 'charset="utf-8"' in text
    assert "cafe\u0301" in text  # NFD, not precomposed
    assert "caf\u00e9" in text
    assert "\u0645\u0631\u062d\u0628\u0627" in text
    assert b"cafe\xcc\x81" in utf8

    cp = (FIXTURES / "windows1252_declared.html").read_bytes()
    with pytest.raises(UnicodeDecodeError):
        cp.decode("utf-8")
    assert b'charset="windows-1252"' in cp
    assert b"\x80" in cp  # euro, which Latin-1 would leave as U+0080
    assert b"\x97" in cp  # em dash

    bad = (FIXTURES / "malformed_utf8.html").read_bytes()
    with pytest.raises(UnicodeDecodeError):
        bad.decode("utf-8")
    assert b"\xff" in bad
    assert b'charset="utf-8"' in bad


@pytest.mark.parametrize("spider_cls", PARSERS, ids=["scout", "base"])
def test_utf8_parse_normalizes_url_keys_and_keeps_text(make_spider, spider_cls):
    spider = make_spider(spider_cls)
    response = _response("utf8_mixed.html", b"text/html; charset=utf-8")

    assert response.encoding == "utf-8"
    assert response.encoding != locale.getpreferredencoding(False)
    assert response.css("title::text").get() == UTF8_TITLE
    paragraph = response.css("p::text").get()
    assert paragraph is not None
    # Body text is not composed: both the NFC and NFD cafe spellings survive.
    assert "caf\u00e9" in paragraph
    assert "cafe\u0301" in paragraph
    assert "\u0645\u0631\u062d\u0628\u0627 \u0628\u0627\u0644\u0639\u0627\u0644\u0645" in paragraph
    assert "\U0001f468\u200d\U0001f469\u200d\U0001f467\u200d\U0001f466" in paragraph
    paragraph.encode("utf-8")  # no lone surrogates

    requests = _requests(spider, response)
    urls = {request.url for request in requests}
    assert urls == UTF8_URLS
    assert len(requests) == len(UTF8_URLS)
    assert all(url.isascii() for url in urls)
    assert all(request.meta.get("depth") == 1 and request.callback == spider.parse for request in requests)

    # NFC, NFD and fullwidth are one dedup key; IDN and punycode are one key.
    assert url_hash("https://example.com/dept/caf\u00e9") == url_hash(CAFE)
    assert url_hash("https://example.com/dept/cafe\u0301") == url_hash(CAFE)
    assert url_hash("https://example.com/dept/\uff23\uff41f\u00e9") == url_hash(CAFE)
    assert url_hash("https://example.com/dept/\u0645\u0631\u062d\u0628\u0627") == url_hash(RTL)
    assert url_hash("https://B\u00dcCHER.example.com/Pfad") == url_hash(IDN)
    assert url_hash("https://xn--bcher-kva.example.com/pfad") == url_hash(IDN)


@pytest.mark.parametrize("spider_cls", PARSERS, ids=["scout", "base"])
@pytest.mark.parametrize("content_type", [
    b"text/html; charset=windows-1252",
    b"TEXT/HTML ; Charset=Windows-1252",
    b"text/html",  # no header charset: <meta charset="windows-1252"> decides
])
def test_declared_windows1252_matches_utf8_cafe_key(make_spider, spider_cls, content_type):
    spider = make_spider(spider_cls)
    response = _response("windows1252_declared.html", content_type)

    assert response.encoding == "cp1252"
    assert response.css("title::text").get() == CP1252_TITLE
    assert response.css("p::text").get() == CP1252_PARAGRAPH
    # Not decoded as Latin-1 (U+0080 / U+0097) and not as the cp037 locale.
    assert "\u20ac" in CP1252_TITLE and "\u2014" in CP1252_TITLE
    assert "\u0080" not in response.text and "\u0097" not in response.text

    requests = _requests(spider, response)
    urls = {request.url for request in requests}
    assert urls == CP1252_URLS
    assert len(requests) == len(CP1252_URLS)
    assert all(url.isascii() for url in urls)
    # Same dedup key as the UTF-8 NFC path, despite the different response bytes.
    assert url_hash("https://example.com/dept/caf\u00e9") == url_hash(CAFE)
    assert CAFE in urls


@pytest.mark.parametrize("spider_cls", PARSERS, ids=["scout", "base"])
def test_malformed_utf8_replaces_and_keeps_recoverable_urls(make_spider, spider_cls):
    spider = make_spider(spider_cls)
    response = _response("malformed_utf8.html", b"text/html; charset=utf-8")

    assert response.encoding == "utf-8"
    assert response.css("title::text").get() == "Caf\ufffd"
    paragraph = response.css("p::text").get()
    assert paragraph is not None
    assert "na\u00efve" in paragraph  # valid UTF-8 beside the bad byte still decodes
    assert "\ufffd" in paragraph
    assert response.text.count("\ufffd") == 2
    response.text.encode("utf-8")

    requests = _requests(spider, response)
    urls = {request.url for request in requests}
    # Exactly the three hrefs. decodeURIComponent("%FF%FE") is a decode failure:
    # it is not turned into a replacement URL and it does not raise.
    assert urls == MALFORMED_URLS
    assert len(requests) == len(MALFORMED_URLS)
    assert all(url.isascii() and "\ufffd" not in url for url in urls)
