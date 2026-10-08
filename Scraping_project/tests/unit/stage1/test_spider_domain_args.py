"""Running for a different domain (#56): -a allowed_domains / start_urls and config fallback."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.stage1.experimental import base_spider as bs

pytestmark = [pytest.mark.unit, pytest.mark.stage1]


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, []),
        ("", []),
        ("example.org", ["example.org"]),
        ("example.org,docs.example.org", ["example.org", "docs.example.org"]),
        ("a.org, b.org  c.org", ["a.org", "b.org", "c.org"]),
        (["a.org", " b.org ", ""], ["a.org", "b.org"]),
        (("x.org",), ["x.org"]),
    ],
)
def test_as_list(value, expected):
    assert bs._as_list(value) == expected


def _spider(config_domains=None, **kwargs):
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "stage1.allowed_domains": config_domains,
        "stage1.js_confidence_threshold": 0.5,
        "stage1.batch_size": 50,
    }.get(key, default)
    delta = MagicMock()
    delta.read.return_value = [{"url": "https://seed.example/"}]
    with patch.object(bs, "get_config", return_value=config), patch.object(bs, "get_delta", return_value=delta), \
            patch.object(bs, "get_redis", return_value=MagicMock()), patch.object(bs, "URLProcessor"):
        return bs.BaseSpider(name="t", **kwargs)


def test_cli_string_argument_is_split_not_iterated_by_character():
    s = _spider(allowed_domains="example.org,docs.example.org", start_urls="https://example.org/")
    assert s.allowed_domains == ["example.org", "docs.example.org"]
    assert s.start_urls == ["https://example.org/"]


def test_config_domains_are_used_when_no_argument():
    assert _spider(config_domains=["example.org"]).allowed_domains == ["example.org"]


def test_uconn_default_and_seed_table_fallback():
    s = _spider()
    assert s.allowed_domains == ["uconn.edu"]
    assert s.start_urls == ["https://seed.example/"]  # from the seed_urls table
