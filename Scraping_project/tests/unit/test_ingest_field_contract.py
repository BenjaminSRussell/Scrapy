"""Python producer <-> Rust ingest field contract (#531)."""

import json
import re
from pathlib import Path

from scrapy import Spider

from src.core.ingest_contract import REQUIRED_INGEST_FIELDS, missing_ingest_fields
from src.pipelines import MetadataPipeline

MAIN_RS = Path(__file__).resolve().parents[2] / "kafka-delta-ingest" / "src" / "main.rs"


def _rust_required_fields():
    src = MAIN_RS.read_text()
    match = re.search(r"const REQUIRED_INGEST_FIELDS: \[&str; \d+\] = \[([^\]]*)\];", src)
    assert match, "REQUIRED_INGEST_FIELDS constant not found in main.rs"
    return tuple(re.findall(r'"([^"]+)"', match.group(1)))


def _rust_timestamp_pattern():
    src = MAIN_RS.read_text()
    match = re.search(r'"scraped_at_utc": \{\s*"type": "string",\s*"pattern": "([^"]+)"', src)
    assert match
    return match.group(1).replace("\\\\", "\\")


def test_python_and_rust_required_fields_match():
    assert _rust_required_fields() == REQUIRED_INGEST_FIELDS


def test_rust_schema_uses_the_shared_constant():
    src = MAIN_RS.read_text()
    assert '"required": REQUIRED_INGEST_FIELDS' in src


def test_rust_write_path_never_empty_fills_required_fields():
    src = MAIN_RS.read_text()
    assert 'unwrap_or("")' not in src


def test_metadata_pipeline_output_satisfies_ingest_contract():
    spider = Spider(name="discovery")
    item = MetadataPipeline().process_item({"url": "https://uconn.edu/a", "title": "A"}, spider)
    wire = json.loads(json.dumps(item, default=str))  # what KafkaPipeline produces
    assert missing_ingest_fields(wire) == []
    assert re.match(_rust_timestamp_pattern(), wire["scraped_at_utc"])


def test_missing_ingest_fields_flags_missing_empty_and_non_string():
    ok = {"url": "u", "scraped_at_utc": "2026-10-07T00:00:00Z", "spider_name": "s"}
    assert missing_ingest_fields(ok) == []
    assert missing_ingest_fields({**ok, "spider_name": ""}) == ["spider_name"]
    assert missing_ingest_fields({**ok, "url": None}) == ["url"]
    drifted = {k: v for k, v in ok.items() if k != "spider_name"} | {"spider": "s"}
    assert missing_ingest_fields(drifted) == ["spider_name"]
    assert missing_ingest_fields({**ok, "scraped_at_utc": 123}) == ["scraped_at_utc"]
