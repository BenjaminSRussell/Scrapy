"""Scraped-item schema id stub and the optional registry profile (#654)."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src import pipelines as p
from src.schemas import (
    SCHEMA_ID_HEADER,
    SCRAPED_ITEM_SCHEMA_ID,
    SCRAPED_ITEM_SCHEMA_SUBJECT,
    UnknownSchemaId,
    accept_schema_id,
    schema_id_headers,
)

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT.parent
MAIN_RS = ROOT / "kafka-delta-ingest" / "src" / "main.rs"
SPIDER = SimpleNamespace(name="scout")


def test_published_id_matches_the_rust_consumer():
    src = MAIN_RS.read_text(encoding="utf-8")
    id_match = re.search(r"const SCRAPED_ITEM_SCHEMA_ID: i32 = (\d+);", src)
    subject_match = re.search(r'const SCRAPED_ITEM_SCHEMA_SUBJECT: &str = "([^"]+)";', src)
    assert id_match and int(id_match.group(1)) == SCRAPED_ITEM_SCHEMA_ID
    assert subject_match and subject_match.group(1) == SCRAPED_ITEM_SCHEMA_SUBJECT
    assert 'reason: "unknown_schema_id"' in src
    assert "schema_id_header_value" in src


def test_header_carries_the_published_id():
    headers = schema_id_headers()
    assert headers == [(SCHEMA_ID_HEADER, b"1")]
    assert accept_schema_id(None) == SCRAPED_ITEM_SCHEMA_ID
    assert accept_schema_id(1) == 1
    assert accept_schema_id("1") == 1
    assert accept_schema_id(b"1") == 1
    assert accept_schema_id("01") == 1


@pytest.mark.parametrize("raw", [99, "99", b"99", "nope", b"", "", "+1", "-1", " 1", True, 1.5])
def test_unknown_schema_id_is_rejected(raw):
    with pytest.raises(UnknownSchemaId):
        accept_schema_id(raw)
    if not isinstance(raw, bool) and isinstance(raw, int):
        with pytest.raises(UnknownSchemaId):
            schema_id_headers(raw)


class _Producer:
    def __init__(self):
        self.sent = []

    def produce(self, topic, value, callback, key=None, headers=None):
        self.sent.append({"topic": topic, "key": key, "value": value, "headers": headers})
        callback(None, None)

    def poll(self, timeout=0):
        return 0

    def flush(self, timeout=None):
        return 0


def test_kafka_pipeline_publishes_schema_id_on_the_message(tmp_path):
    prod = _Producer()
    pipe = p.KafkaPipeline("broker:9092", "scraped-items", spill_dir=tmp_path, retry_backoff=0)
    pipe.producer = prod
    item = {"url": "https://uconn.edu/a", "url_hash": "h", "spider_name": "scout"}
    assert pipe.process_item(item, SPIDER) is item
    assert len(prod.sent) == 1
    assert prod.sent[0]["headers"] == [("schema_id", b"1")]
    assert prod.sent[0]["value"].startswith(b"{")  # body stays JSON


def test_compose_profile_is_an_optional_example():
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    svc = services["schema-registry"]
    assert svc["profiles"] == ["schema-registry"]
    assert "core" not in svc["profiles"] and "streaming" not in svc["profiles"]
    assert svc["image"] == "confluentinc/cp-schema-registry:7.7.1"
    assert svc["depends_on"] == ["kafka"]
    assert "127.0.0.1:8081:8081" in svc["ports"]
    compose_doc = (ROOT / "COMPOSE.md").read_text(encoding="utf-8")
    assert "schema-registry" in compose_doc
    assert "scraped-items-value" in compose_doc
    lib = (ROOT / "scripts" / "compose_lib.sh").read_text(encoding="utf-8")
    assert "COMPOSE_PROFILES:-core,monitoring" in lib.replace(" ", "")
    shutdown = (ROOT / "shutdown.py").read_text(encoding="utf-8")
    assert '"schema-registry"' in shutdown
    adr = (REPO / "docs" / "adr" / "0004-kafka-schema-registry-stub.md").read_text(encoding="utf-8")
    assert "scraped-items-value" in adr and "schema_id" in adr
    assert "0004-kafka-schema-registry-stub.md" in (REPO / "docs" / "adr" / "README.md").read_text(encoding="utf-8")
