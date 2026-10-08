"""src/items.py: OffsiteCandidateItem fields and required-field contract (#247)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from scrapy.exceptions import DropItem

from src.items import OffsiteCandidateItem

pytestmark = pytest.mark.unit

FULL = {
    "source_page": "https://uconn.edu/a",
    "external_url": "https://example.org/x",
    "anchor_text": "Example",
    "context": "see Example for more",
    "discovered_at": "2026-10-08T11:00:00+00:00",
}


def test_declared_fields_are_the_offsite_table_columns():
    assert set(OffsiteCandidateItem.fields) == set(FULL)
    assert set(OffsiteCandidateItem.REQUIRED_FIELDS) <= set(OffsiteCandidateItem.fields)


def test_unknown_field_is_rejected_by_scrapy():
    with pytest.raises(KeyError):
        OffsiteCandidateItem(**FULL, extra="nope")


def test_complete_item_validates_and_round_trips():
    item = OffsiteCandidateItem(**FULL)
    item.validate()
    assert item.missing_required() == []
    assert dict(item) == FULL


def test_optional_fields_may_be_absent():
    item = OffsiteCandidateItem(**{k: FULL[k] for k in OffsiteCandidateItem.REQUIRED_FIELDS})
    assert item.missing_required() == []


@pytest.mark.parametrize("field", OffsiteCandidateItem.REQUIRED_FIELDS)
@pytest.mark.parametrize("bad", ["<absent>", None, "", "   "])
def test_missing_required_field_fails_clearly(field, bad):
    data = dict(FULL)
    if bad == "<absent>":
        del data[field]
    else:
        data[field] = bad
    item = OffsiteCandidateItem(**data)
    assert item.missing_required() == [field]
    with pytest.raises(ValueError, match=rf"missing required field\(s\): {field}$"):
        item.validate()


def test_all_missing_fields_are_named_in_order():
    item = OffsiteCandidateItem(anchor_text="x")
    assert item.missing_required() == ["source_page", "external_url", "discovered_at"]


def test_spider_factory_emits_a_valid_item():
    """base_spider._create_offsite_item is the producer; it must satisfy the contract."""
    from scrapy.http import HtmlResponse

    from src.stage1.experimental.base_spider import BaseSpider

    body = b'<html><body><p>Read <a href="https://example.org/x">Example</a> now</p></body></html>'
    resp = HtmlResponse("https://uconn.edu/a", body=body, encoding="utf-8")
    spider = BaseSpider.__new__(BaseSpider)
    item = spider._create_offsite_item(resp, "https://example.org/x")
    assert isinstance(item, OffsiteCandidateItem)
    assert item.missing_required() == []


def test_offsite_pipeline_drops_invalid_items_and_keeps_valid_ones():
    from src.pipelines import OffsiteCandidatePipeline

    added = []
    pipe = OffsiteCandidatePipeline.__new__(OffsiteCandidatePipeline)
    pipe.batch = SimpleNamespace(rows_written=0, add=added.append)
    pipe.items_processed = 0
    spider = SimpleNamespace(name="scout")

    with pytest.raises(DropItem, match="missing required field\\(s\\): external_url"):
        pipe.process_item(OffsiteCandidateItem(**{**FULL, "external_url": ""}), spider)
    assert added == [] and pipe.items_processed == 0

    good = OffsiteCandidateItem(**FULL)
    assert pipe.process_item(good, spider) is good
    assert added == [FULL]

    plain = {"url": "https://uconn.edu/"}
    assert pipe.process_item(plain, spider) is plain  # other items pass through untouched
