"""rustmapper JSONL fixtures ↔ SeedManager accept/reject (#936).

No network. An incompatible field fails before any seed write. Schema-valid
lines go through SeedManager, which accepts crawlable URLs and rejects the rest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.lakehouse import SeedManager
from src.lakehouse.lakehouse_manager import InMemoryBackend
from src.lakehouse.seed_manager import default_url_hasher
from src.stage1.rustmapper_contract import (
    RUSTMAPPER_JSONL_FIELDS,
    RUSTMAPPER_SOURCE_SPIDER,
    SEED_ROW_FIELDS,
    RustmapperContractError,
    emit_jsonl_line,
    ingest_rustmapper_jsonl,
    load_rustmapper_jsonl,
    parse_rustmapper_record,
)
from src.utils.validation import url_rejection_reason

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rustmapper_seed"
ACCEPT = FIXTURES / "accept.jsonl"
REJECT = FIXTURES / "reject.jsonl"
INCOMPATIBLE = FIXTURES / "incompatible.jsonl"
SOURCE_URL = "https://uconn.edu/"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """SeedManager must not dial Redis while it accepts or rejects a fixture."""
    monkeypatch.setenv("OBS_OFFLINE", "1")


def _lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.contract
def test_accept_fixture_matches_emitted_shape():
    """Key order and compact JSON match what cargo test emits."""
    assert _lines(ACCEPT)
    for line in _lines(ACCEPT):
        record = parse_rustmapper_record(json.loads(line))
        assert list(record) == list(RUSTMAPPER_JSONL_FIELDS)
        assert line == emit_jsonl_line(record)


@pytest.mark.contract
def test_accept_fixture_seed_manager_inserts_rows():
    backend = InMemoryBackend()
    result = ingest_rustmapper_jsonl(
        SeedManager(backend),
        ACCEPT,
        source_url=SOURCE_URL,
        enqueue_stage2=True,
    )
    assert result["rejected"] == 0
    assert result["seed_inserted"] == 2
    assert result["stage2_enqueued"] == 2

    seeds = backend.read("seed_urls")
    assert len(seeds) == 2
    urls = {row["url"] for row in seeds}
    assert urls == {"https://uconn.edu/about", "https://admissions.uconn.edu/apply"}
    for row in seeds:
        assert set(row) == set(SEED_ROW_FIELDS)
        assert row["source_spider"] == RUSTMAPPER_SOURCE_SPIDER
        assert row["source_url"] == SOURCE_URL
        assert row["url_hash"] == default_url_hasher(row["url"])
        assert isinstance(row["discovered_at"], str) and row["discovered_at"]

    queued = backend.read("stage2_queue")
    assert len(queued) == 2
    for row in queued:
        assert row["status"] == "pending"
        assert row["url"] in urls
        assert row["url_hash"] == default_url_hasher(row["url"])
        assert "enqueued_at" in row


@pytest.mark.contract
def test_reimport_same_jsonl_does_not_duplicate():
    backend = InMemoryBackend()
    manager = SeedManager(backend)
    ingest_rustmapper_jsonl(manager, ACCEPT, source_url=SOURCE_URL)
    ingest_rustmapper_jsonl(manager, ACCEPT, source_url=SOURCE_URL)
    assert len(backend.read("seed_urls")) == 2


@pytest.mark.contract
def test_reject_fixture_is_schema_valid_but_seed_manager_rejects():
    records = load_rustmapper_jsonl(REJECT)
    assert len(records) == 5
    assert all(url_rejection_reason(record["url"]) is not None for record in records)
    for line in _lines(REJECT):
        assert line == emit_jsonl_line(json.loads(line))

    backend = InMemoryBackend()
    result = ingest_rustmapper_jsonl(SeedManager(backend), REJECT, source_url=SOURCE_URL)
    assert result["seed_inserted"] == 0
    assert result["stage2_enqueued"] == 0
    assert result["rejected"] == 5
    assert not backend.table_exists("seed_urls")


@pytest.mark.contract
def test_incompatible_field_fails():
    """Unknown, missing, or wrong-typed fields fail. SeedManager is not called."""
    backend = InMemoryBackend()
    with pytest.raises(RustmapperContractError, match="incompatible field"):
        ingest_rustmapper_jsonl(SeedManager(backend), INCOMPATIBLE, source_url=SOURCE_URL)
    assert not backend.table_exists("seed_urls")

    for line in _lines(INCOMPATIBLE):
        with pytest.raises(RustmapperContractError, match="incompatible field"):
            parse_rustmapper_record(json.loads(line))


@pytest.mark.contract
def test_extra_field_on_a_valid_record_fails():
    record = json.loads(_lines(ACCEPT)[0])
    record["priority"] = 1
    with pytest.raises(RustmapperContractError, match="incompatible field: priority"):
        parse_rustmapper_record(record)
