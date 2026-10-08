"""#227 / #302: one bronze contract for the Python producer and the Rust ingest.

BaseRecordSchema required url, source_url, title and publication_date while
kafka-delta-ingest requires url, scraped_at_utc and spider_name. Pages without
an extractable date or title were dropped before Kafka (#302), and the two
validators disagreed on what a valid record is (#227).
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src import pipelines
from src.pipelines import SchemaValidationPipeline
from src.schemas import BRONZE_REQUIRED_FIELDS, BaseRecordSchema

MAIN_RS = Path(__file__).resolve().parents[2] / "kafka-delta-ingest" / "src" / "main.rs"
SPIDER = SimpleNamespace(name="scout")


def _rust_required() -> tuple[str, ...]:
    m = re.search(r"const REQUIRED_INGEST_FIELDS: \[&str; \d+\] = \[([^\]]*)\];", MAIN_RS.read_text())
    assert m, "REQUIRED_INGEST_FIELDS not found in main.rs"
    return tuple(re.findall(r'"([^"]+)"', m.group(1)))


def test_python_and_rust_require_the_same_fields():
    python_required = {n for n, f in BaseRecordSchema.model_fields.items() if f.is_required()}
    assert python_required == set(BRONZE_REQUIRED_FIELDS) == set(_rust_required())


def test_rust_schema_is_built_from_the_shared_list():
    src = MAIN_RS.read_text()
    assert '"required": REQUIRED_INGEST_FIELDS' in src


def test_minimal_bronze_record_is_valid():
    rec = BaseRecordSchema(url="https://uconn.edu/a", scraped_at_utc="2026-01-01T00:00:00Z", spider_name="scout")
    assert rec.publication_date is None and rec.title is None
    assert rec.source_url == rec.url  # defaults to url


@pytest.mark.parametrize("missing", BRONZE_REQUIRED_FIELDS)
def test_each_required_field_is_enforced(missing):
    data = {"url": "https://uconn.edu/a", "scraped_at_utc": "2026-01-01T00:00:00Z", "spider_name": "scout"}
    del data[missing]
    with pytest.raises(ValidationError):
        BaseRecordSchema(**data)


def test_empty_publication_date_is_none_and_bad_one_still_fails():
    base = {"url": "https://uconn.edu/a", "scraped_at_utc": "2026-01-01T00:00:00Z", "spider_name": "scout"}
    assert BaseRecordSchema(**base, publication_date="").publication_date is None
    with pytest.raises(ValidationError):
        BaseRecordSchema(**base, publication_date="not a date")


class Counter:
    def __init__(self):
        self.calls = []

    def labels(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(inc=lambda *a: None)


@pytest.fixture
def counters(monkeypatch):
    drops, missing = Counter(), Counter()
    monkeypatch.setattr(pipelines, "SCHEMA_DROPS", drops)
    monkeypatch.setattr(pipelines, "MISSING_PUBLICATION_DATE", missing)
    return drops, missing


def test_page_without_date_or_title_is_kept_and_counted(counters):
    drops, missing = counters
    item = {"url": "https://uconn.edu/no-date", "content": "body"}
    out = SchemaValidationPipeline(enabled=True).process_item(item, SPIDER)
    assert out["validation_status"] is True
    assert out["spider_name"] == "scout" and out["scraped_at_utc"]
    assert out["publication_date"] is None
    assert missing.calls == [{"spider": "scout"}] and drops.calls == []


def test_drop_is_counted_by_field(counters):
    from scrapy.exceptions import DropItem
    drops, _ = counters
    item = {"url": "https://uconn.edu/x", "tuition_cost": -5}
    with pytest.raises(DropItem):
        SchemaValidationPipeline(enabled=True).process_item(item, SPIDER)
    assert drops.calls == [{"spider": "scout", "field": "tuition_cost"}]


def test_existing_provenance_is_not_overwritten(counters):
    item = {"url": "https://uconn.edu/a", "scraped_at_utc": "2025-05-05T05:05:05Z", "spider_name": "depth",
            "publication_date": "2025-01-01T00:00:00Z"}
    out = SchemaValidationPipeline(enabled=True).process_item(item, SPIDER)
    assert out["spider_name"] == "depth"
    assert out["scraped_at_utc"].startswith("2025-05-05T05:05:05")
    assert counters[1].calls == []
