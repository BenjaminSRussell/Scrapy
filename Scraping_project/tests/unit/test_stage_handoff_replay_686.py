"""Replay recorded stage-boundary fixtures with no network (#686).

Fixtures live under tests/fixtures/stage_handoff/. Valid records round-trip
through the stage consumers and serializers. Invalid and legacy records hit
the documented error paths (checksum, schema, cast quarantine, DLQ, legacy
table name).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from scrapy.exceptions import DropItem

from src.core.ingest_contract import missing_ingest_fields
from src.core.models import (
    ErrorRecord,
    Stage1Discovery,
    Stage2Analysis,
    Stage3Summary,
    Stage4LargeDocSummary,
)
from src.core.schemas import get_schema
from src.lakehouse.lakehouse_manager import cast_rows_to_schema
from src.pipelines import KafkaPipeline, SchemaValidationPipeline, _canonical_queue_row, is_queue_routing_item
from src.schemas import BaseRecordSchema
from src.stage1.discovery_handoff import (
    DiscoveryEmptyNeedsAck,
    DiscoveryHandoffError,
    checksum_urls,
    consume_rustmapper_handoff,
    read_handoff_json,
    require_ingestible,
    write_handoff_json,
)
from src.stage2.stage2_worker import ensure_url_hash, split_stage2_results
from src.stage3.stage3_worker import FingerprintStore, Stage3Worker, select_quality_documents
from src.stage4.entity_summarization import decode_facts_json, serialize_fact
from src.stage4.stage4_worker import ANALYSIS_TABLE, QUEUE_TABLE, Stage4Worker
from src.utils.dead_letter_queue import DeadLetterQueue
from src.utils.ssrf import ssrf_block_reason
from src.utils.url_canon import url_hash

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "stage_handoff"

# One recorded file per stage boundary. Invalid and legacy siblings sit beside them.
BOUNDARY_FILES = (
    "discovery_success.json",
    "discovery_sitemap.jsonl",
    "stage1_to_stage2.jsonl",
    "stage1_discovery.jsonl",
    "stage2_to_stage3.jsonl",
    "stage2_to_stage4.jsonl",
    "stage2_analysis_fallback.jsonl",
    "stage2_errors.jsonl",
    "stage3_summaries.jsonl",
    "stage3_summaries_legacy.jsonl",
    "stage4_summaries.jsonl",
    "bronze_scraped_items.jsonl",
    "entity_facts.json",
    "error_log.jsonl",
    "dlq/stage1.json",
    "dlq/stage2.json",
    "dlq/stage3.json",
    "dlq/stage4_legacy.json",
)

DISCOVERED = [
    "https://www.example.edu/academics",
    "https://www.example.edu/admissions",
    "https://www.example.edu/research",
]
PROGRAMS = "https://www.example.edu/programs"
PROGRAMS_TEXT = (
    "Tuition is published each spring. Housing rates follow in May. Aid deadlines are in March."
)
AID_TEXT = (
    "Laboratory safety manuals describe fume hood certification, chemical storage cabinets, "
    "eyewash stations, and spill kits for the engineering complex."
)
AID_SUMMARY = AID_TEXT
CATALOG_TEXT = "Complete body of the large catalog document."
LEGACY_TEXT = "Legacy row without a status field."
DUP_TEXT = "First copy of the duplicate document."


def load_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_jsonl(name: str) -> list[dict]:
    rows = []
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def coerce_ts(value):
    """ISO-8601 fixture strings become datetimes; unparseable values stay put."""
    if not isinstance(value, str):
        return value
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return value


def with_timestamps(row: dict, fields: tuple[str, ...]) -> dict:
    out = dict(row)
    for field in fields:
        if field in out and out[field] is not None:
            out[field] = coerce_ts(out[field])
    return out


class _Spider:
    def __init__(self, name: str) -> None:
        self.name = name


def test_each_stage_boundary_has_a_recorded_fixture():
    for name in BOUNDARY_FILES:
        assert (FIXTURES / name).is_file(), name


def test_discovery_success_fixture_replays_to_the_recorded_urls(tmp_path):
    handoff = read_handoff_json(FIXTURES / "discovery_success.json")
    assert handoff.is_success
    assert handoff.discovered == DISCOVERED
    assert handoff.checksum == checksum_urls(DISCOVERED)
    assert require_ingestible(handoff) == DISCOVERED

    path = tmp_path / "discovery_success.json"
    write_handoff_json(path, handoff)
    assert read_handoff_json(path).to_dict() == handoff.to_dict()


def test_discovery_sitemap_jsonl_skips_malformed_and_keeps_legacy_loc():
    handoff = consume_rustmapper_handoff(
        FIXTURES / "discovery_sitemap.jsonl",
        discovery_source="rustmapper",
        job_id="job-686",
        site="www.example.edu",
    )
    assert handoff.is_success
    assert handoff.discovered == DISCOVERED
    assert handoff.job_id == "job-686"
    assert require_ingestible(handoff) == DISCOVERED


def test_discovery_empty_failed_checksum_and_malformed_follow_the_contract():
    empty = read_handoff_json(FIXTURES / "discovery_empty.json")
    with pytest.raises(DiscoveryEmptyNeedsAck, match="ack/skip Stage2"):
        require_ingestible(empty)
    assert require_ingestible(empty, ack_empty=True) == []

    failed = consume_rustmapper_handoff(
        FIXTURES / "discovery_sitemap.jsonl",
        FIXTURES / "discovery_failed.json",
    )
    assert failed.is_failed
    with pytest.raises(DiscoveryHandoffError, match="rustmapper crashed"):
        require_ingestible(failed)

    mismatch = read_handoff_json(FIXTURES / "discovery_bad_checksum.json")
    with pytest.raises(DiscoveryHandoffError, match="checksum mismatch"):
        require_ingestible(mismatch)

    with pytest.raises(ValueError):
        read_handoff_json(FIXTURES / "discovery_invalid_status.json")
    with pytest.raises(json.JSONDecodeError):
        read_handoff_json(FIXTURES / "discovery_malformed.json")

    bare = consume_rustmapper_handoff(FIXTURES / "discovery_sitemap_empty.jsonl")
    assert bare.is_empty
    with pytest.raises(DiscoveryEmptyNeedsAck):
        require_ingestible(bare)


def test_legacy_discovery_handoff_without_checksum_is_still_ingestible():
    handoff = read_handoff_json(FIXTURES / "discovery_legacy.json")
    assert handoff.is_success
    assert handoff.discovered == ["https://www.example.edu/about"]
    assert handoff.checksum == checksum_urls(handoff.discovered)
    assert "engine" not in handoff.to_dict()
    assert require_ingestible(handoff) == ["https://www.example.edu/about"]


def test_stage1_queue_handoff_canonicalizes_and_rejects_ssrf():
    rows = load_jsonl("stage1_to_stage2.jsonl")
    assert all(is_queue_routing_item(row) for row in rows)
    canonical = [_canonical_queue_row(row) for row in rows]
    assert canonical[0]["url"] == "https://www.example.edu/tuition"
    assert canonical[0]["url_hash"] == "deb2cb6db4047384"
    assert canonical[0]["url_hash"] == url_hash(canonical[0]["url"])
    assert canonical[0]["target_stage"] == "stage2"
    assert canonical[0]["queued_at"] == "2024-01-20T10:30:00Z"
    assert canonical[1]["url"] == "https://www.example.edu/js-app"
    assert canonical[1]["url_hash"] == "187ca3618796dbc8"
    assert canonical[1]["target_spider"] == "javascript"
    # Stage 2 reads status == "pending" (stage2_worker). Completed stays queued but idle.
    pending = [row for row in canonical if row.get("status") == "pending"]
    assert [row["url"] for row in pending] == [
        "https://www.example.edu/tuition",
        "https://www.example.edu/js-app",
    ]

    blocked, content, missing_url = load_jsonl("stage1_to_stage2_invalid.jsonl")
    assert ssrf_block_reason(blocked["url"]) == "ip_loopback"
    assert is_queue_routing_item(blocked)
    assert not is_queue_routing_item(content)
    assert "url_hash" not in _canonical_queue_row(missing_url)

    queued = [with_timestamps(row, ("queued_at",)) for row in canonical]
    table, kept, failures = cast_rows_to_schema(queued, get_schema("stage2_queue"))
    assert failures == [] and kept == queued and table is not None
    assert table.column("url").to_pylist() == [row["url"] for row in canonical]
    assert table.column("status").to_pylist() == ["pending", "pending", "completed"]


def test_stage1_discovery_fixture_round_trips_and_rejects_bad_rows():
    row = load_jsonl("stage1_discovery.jsonl")[0]
    record = Stage1Discovery.model_validate(row)
    dumped = record.model_dump(mode="json")
    assert dumped == {
        "url": PROGRAMS,
        "url_hash": "a25d8b6fad7002b1ac6877fe8c53d353a909fa669657266c17ae066543253e28",
        "discovered_at": "2024-01-20T10:30:00Z",
        "is_heavy": False,
        "is_dynamic": False,
        "depth": 1,
        "parent_url": "https://www.example.edu/",
        "status": "pending",
        "queued_at": "2024-01-20T10:31:00Z",
    }
    assert Stage1Discovery.model_validate(dumped).model_dump(mode="json") == dumped

    legacy = load_jsonl("stage1_discovery_legacy.jsonl")[0]
    normalized = Stage1Discovery.model_validate(legacy)
    assert normalized.url_hash == dumped["url_hash"]
    assert normalized.is_heavy is False and normalized.depth == 0
    assert normalized.model_dump(mode="json")["url_hash"] == normalized.url_hash

    for bad in load_jsonl("stage1_discovery_invalid.jsonl"):
        with pytest.raises(ValidationError):
            Stage1Discovery.model_validate(bad)

    table, _, failures = cast_rows_to_schema(
        [with_timestamps(row, ("queued_at", "discovered_at"))],
        get_schema("stage1_discovery"),
    )
    assert failures == [] and table is not None
    assert table.column("url").to_pylist() == [PROGRAMS]
    assert table.column("depth").to_pylist() == [1]


def test_stage2_analysis_replays_into_stage3_summaries():
    docs = load_jsonl("stage2_to_stage3.jsonl")
    assert [Stage2Analysis.model_validate(doc).model_dump(mode="json")["url"] for doc in docs] == [
        PROGRAMS,
        "https://www.example.edu/housing",
        "https://www.example.edu/aid",
    ]
    programs = Stage2Analysis.model_validate(docs[0]).model_dump(mode="json")
    assert programs["text_content"] == PROGRAMS_TEXT
    assert programs["word_count"] == 15
    assert programs["content_length"] == 90
    assert programs["quality_score"] == 0.82
    assert programs["keywords"] == ["tuition", "housing"]
    assert programs["processed_at"] == "2024-01-20T12:00:00Z"
    assert programs["is_low_quality"] is False and programs["is_massive_doc"] is False

    quality = select_quality_documents(docs)
    assert [doc["url"] for doc in quality] == [doc["url"] for doc in docs]
    unique, summaries = asyncio.run(_replay_stage3(quality))
    assert [doc["url"] for doc in unique] == [PROGRAMS, "https://www.example.edu/aid"]
    assert _summary_body(summaries[0]) == {
        "url": PROGRAMS,
        "url_hash": docs[0]["url_hash"],
        "summary": PROGRAMS_TEXT,
        "word_count": 15,
        "keywords": ["tuition", "housing"],
        "quality_score": 0.82,
    }
    assert _summary_body(summaries[1]) == {
        "url": "https://www.example.edu/aid",
        "url_hash": docs[2]["url_hash"],
        "summary": AID_SUMMARY,
        "word_count": 19,
        "keywords": ["aid"],
        "quality_score": 0.64,
    }

    accepted, quarantined = split_stage2_results(docs + load_jsonl("stage2_errors.jsonl") + [None, "skip"])
    assert [row["url"] for row in accepted] == [doc["url"] for doc in docs]
    assert quarantined[0]["error_message"] == "soft_ban:cloudflare"
    assert quarantined[0]["has_error"] is True

    table, kept, failures = cast_rows_to_schema(
        [with_timestamps(doc, ("processed_at",)) for doc in docs],
        get_schema("stage2_page_analysis"),
    )
    assert failures == [] and len(kept) == 3 and table is not None
    assert table.column("title").to_pylist() == ["Programs", "Housing duplicate", "Aid"]


def test_legacy_stage2_rows_skip_or_normalize_and_bad_rows_fail():
    legacy = load_jsonl("stage2_to_stage3_legacy.jsonl")
    selected = select_quality_documents(legacy)
    # Missing is_low_quality is low quality. The hash-less about row is kept.
    assert [doc["url"] for doc in selected] == [PROGRAMS, "https://www.example.edu/about"]
    with pytest.raises(ValidationError):
        Stage2Analysis.model_validate(legacy[0])

    normalized = Stage2Analysis.model_validate(legacy[1])
    assert normalized.url_hash == legacy[1]["url_hash"].lower()
    assert normalized.keywords == ["tuition", "housing"]

    hashed = ensure_url_hash([dict(legacy[2])])
    assert hashed[0]["url_hash"] == url_hash("https://www.example.edu/about")
    assert hashed[0]["url_hash"] == "dbfbb6591bd93f9d"

    for bad in load_jsonl("stage2_to_stage3_invalid.jsonl"):
        with pytest.raises(ValidationError):
            Stage2Analysis.model_validate(bad)

    cast_rows = [with_timestamps(row, ("processed_at",)) for row in load_jsonl("stage2_cast_invalid.jsonl")]
    table, kept, failures = cast_rows_to_schema(cast_rows, get_schema("stage2_page_analysis"))
    assert table is None and kept == []
    assert {(f["row_index"], f["column"]) for f in failures} >= {(0, "word_count"), (1, "processed_at")}


def test_legacy_stage3_table_is_already_processed_and_summaries_round_trip():
    current = load_jsonl("stage3_summaries.jsonl")[0]
    legacy = load_jsonl("stage3_summaries_legacy.jsonl")[0]
    record = Stage3Summary.model_validate(current)
    assert record.model_dump(mode="json") == {
        "url": PROGRAMS,
        "url_hash": current["url_hash"],
        "discovered_at": None,
        "summary": "This summary is long enough to pass.",
        "word_count": 15,
        "keywords": ["tuition", "housing"],
        "quality_score": 0.82,
        "timestamp": "2024-01-20T13:00:00Z",
    }
    legacy_record = Stage3Summary.model_validate({**legacy, "url_hash": legacy["url_hash"].upper()})
    assert legacy_record.url_hash == legacy["url_hash"]
    assert legacy_record.summary == "Legacy stage4_summaries row kept for dedup."

    worker = Stage3Worker.__new__(Stage3Worker)

    class _Delta:
        def read(self, table: str):
            if table == "stage3_summaries":
                return [current]
            if table == "stage4_summaries":
                return [legacy]
            return []

    worker.delta = _Delta()
    assert worker._processed_hashes() == {current["url_hash"], legacy["url_hash"]}

    for bad in load_jsonl("stage3_summaries_invalid.jsonl"):
        with pytest.raises(ValidationError):
            Stage3Summary.model_validate(bad)

    for table_name, row in (("stage3_summaries", current), ("stage4_summaries", legacy)):
        table, _, failures = cast_rows_to_schema(
            [with_timestamps(row, ("timestamp",))],
            get_schema(table_name),
        )
        assert failures == [] and table is not None
        assert table.column("url").to_pylist() == [row["url"]]


def test_stage4_queue_and_analysis_fallback_replay_offline():
    queue = load_jsonl("stage2_to_stage4.jsonl")
    fallback = load_jsonl("stage2_analysis_fallback.jsonl")
    worker = _stage4(queue, fallback)
    done = {"https://www.example.edu/already"}
    pending, queued = worker._pending_from_queue(done)
    assert [doc["url"] for doc in pending] == [
        "https://www.example.edu/catalog",
        "https://www.example.edu/handbook.pdf",
        "https://www.example.edu/archive",
        "https://www.example.edu/legacy-doc",
        "https://www.example.edu/dup",
    ]
    assert "https://www.example.edu/catalog-done" in queued
    assert "https://www.example.edu/already" in queued
    by_url = {doc["url"]: doc for doc in pending}
    assert worker._stored_text(by_url["https://www.example.edu/catalog"]) == CATALOG_TEXT
    assert worker._stored_text(by_url["https://www.example.edu/legacy-doc"]) == LEGACY_TEXT
    assert worker._stored_text(by_url["https://www.example.edu/dup"]) == DUP_TEXT
    assert worker._stored_text(by_url["https://www.example.edu/archive"]) is None
    assert worker._stored_text(by_url["https://www.example.edu/handbook.pdf"]) is None

    fallback_docs = worker._fallback_from_analysis(queued | done)
    assert [doc["url"] for doc in fallback_docs] == [PROGRAMS]
    assert worker._stored_text(fallback_docs[0]) == (
        "Massive program catalog that never reached the stage 4 queue."
    )
    worker.analysis_fallback = False
    assert worker._fallback_from_analysis(set()) == []


def test_stage4_summary_and_fact_fixtures_round_trip_and_reject_bad_rows():
    row = load_jsonl("stage4_summaries.jsonl")[0]
    record = Stage4LargeDocSummary.model_validate(row)
    assert record.model_dump(mode="json") == {
        "url": "https://www.example.edu/catalog",
        "url_hash": row["url_hash"],
        "discovered_at": None,
        "summary": "A catalog summary long enough.",
        "content_type": "html",
        "original_size": 44,
        "summary_size": 29,
        "compression_ratio": 0.659,
        "processed_at": "2024-01-20T14:00:00Z",
        "is_pdf": False,
    }
    legacy_ok, legacy_bad = load_jsonl("stage4_summaries_legacy.jsonl")
    normalized = Stage4LargeDocSummary.model_validate(legacy_ok)
    assert normalized.url_hash == legacy_ok["url_hash"].lower()
    assert normalized.is_pdf is False and normalized.content_type == "text/html"
    with pytest.raises(ValidationError):
        Stage4LargeDocSummary.model_validate(legacy_bad)
    for bad in load_jsonl("stage4_summaries_invalid.jsonl"):
        with pytest.raises(ValidationError):
            Stage4LargeDocSummary.model_validate(bad)

    table, _, failures = cast_rows_to_schema(
        [with_timestamps(row, ("processed_at",))],
        get_schema("stage4_large_doc_summaries"),
    )
    assert failures == [] and table is not None
    assert table.column("summary").to_pylist() == ["A catalog summary long enough."]

    facts = decode_facts_json((FIXTURES / "entity_facts.json").read_text(encoding="utf-8"))
    assert facts[0]["fact_text"] == "In-state tuition is 18000."
    assert facts[0]["publication_date"] == datetime(2024, 1, 15, tzinfo=timezone.utc)
    assert "source_references" in facts[0]
    wire = json.dumps([serialize_fact(facts[0])])
    again = decode_facts_json(wire)
    assert "source_references" not in again[0]
    assert again[0]["fact_text"] == facts[0]["fact_text"]
    assert again[0]["publication_date"] == facts[0]["publication_date"]
    assert again[0]["metadata"] == {"unit": "USD"}

    legacy_fact = decode_facts_json((FIXTURES / "entity_facts_legacy.json").read_text(encoding="utf-8"))[0]
    assert legacy_fact["publication_date"] is None
    assert legacy_fact["metadata"] == {}
    with pytest.raises(ValueError):
        decode_facts_json((FIXTURES / "entity_facts_invalid.json").read_text(encoding="utf-8"))
    with pytest.raises(TypeError):
        decode_facts_json(3)
    assert decode_facts_json("") == []


def test_bronze_fixtures_validate_and_malformed_bytes_are_dead_lettered(tmp_path):
    spider = _Spider("scout")
    pipeline = SchemaValidationPipeline(enabled=True)
    item = pipeline.process_item(load_jsonl("bronze_scraped_items.jsonl")[0], spider)
    assert item["validation_status"] is True
    assert item["url"] == "https://www.example.edu/tuition"
    assert item["source_url"] == item["url"]
    assert item["scraped_at_utc"] == "2024-01-20T10:30:00Z"
    assert item["publication_date"] == "2024-01-15T00:00:00Z"
    assert item["total_cost"] == 33000.0
    assert item["media_type"] == "text"
    assert missing_ingest_fields(item) == []
    again = BaseRecordSchema.model_validate(item).model_dump(mode="json")
    assert BaseRecordSchema.model_validate(again).model_dump(mode="json") == again

    legacy = pipeline.process_item(load_jsonl("bronze_scraped_items_legacy.jsonl")[0], spider)
    assert legacy["source_url"] == "https://www.example.edu/tuition"
    assert legacy["tuition_cost"] == 18000.0
    assert legacy["housing_cost"] == 12000.0
    assert legacy["fees_cost"] == 3000.0
    assert legacy["total_cost"] == 33000.0
    assert legacy["validation_status"] is True
    assert missing_ingest_fields(legacy) == []

    mismatch, missing_url, missing_spider = load_jsonl("bronze_scraped_items_invalid.jsonl")
    with pytest.raises(DropItem):
        pipeline.process_item(mismatch, spider)
    with pytest.raises(DropItem):
        pipeline.process_item(missing_url, spider)
    with pytest.raises(DropItem):
        pipeline.process_item(missing_spider, _Spider(""))

    lines = (FIXTURES / "bronze_malformed.jsonl").read_text(encoding="utf-8").splitlines()
    good = json.loads(lines[0])
    assert missing_ingest_fields(good) == []
    with pytest.raises(json.JSONDecodeError):
        json.loads(lines[1])

    kafka = KafkaPipeline.__new__(KafkaPipeline)
    kafka.topic = "scraped-items"
    kafka.spill_dir = tmp_path
    kafka._dlq = DeadLetterQueue(tmp_path / "dlq")
    kafka._dead_letter(lines[1].encode("utf-8"), "deserialize", "bad json")
    stored = kafka._dlq.list_failed(stage="kafka")
    assert len(stored) == 1
    assert stored[0]["item"]["raw"] == lines[1]


def test_error_log_and_dlq_fixtures_replay_without_services():
    row = load_jsonl("error_log.jsonl")[0]
    record = ErrorRecord.model_validate(row)
    dumped = record.model_dump(mode="json")
    assert dumped["stage"] == "stage2"
    assert dumped["error_message"] == "soft_ban:cloudflare"
    assert dumped["severity"] == "high"
    assert dumped["timestamp"] == "2024-01-20T12:15:00Z"
    assert dumped["context"] == {"signature": "cloudflare"}
    assert ErrorRecord.model_validate(dumped).model_dump(mode="json") == dumped
    with pytest.raises(ValidationError):
        ErrorRecord.model_validate(load_jsonl("error_log_invalid.jsonl")[0])
    table, _, failures = cast_rows_to_schema(
        [with_timestamps(row, ("timestamp",))],
        get_schema("error_log"),
    )
    assert failures == [] and table is not None
    assert table.column("error_type").to_pylist() == ["SoftBan"]

    dlq = DeadLetterQueue(FIXTURES / "dlq")
    listed = dlq.list_failed()
    assert [entry["stage"] for entry in listed] == ["stage4", "stage3", "stage2", "stage1"]
    stage2 = dlq.replay("stage2")
    assert stage2["url"] == "https://www.example.edu/tuition"
    assert stage2["url_hash"] == "deb2cb6db4047384"
    assert stage2["_retry_count"] == 2
    assert stage2["_dlq_entry_id"] == "stage2"
    assert stage2["status"] == "pending"
    legacy = dlq.replay("stage4_legacy")
    assert legacy["url"] == "https://www.example.edu/catalog"
    assert legacy["_retry_count"] == 1
    assert dlq.replay("broken") is None


def _summary_body(summary: dict) -> dict:
    body = dict(summary)
    timestamp = body.pop("timestamp")
    datetime.fromisoformat(timestamp)
    return body


async def _replay_stage3(docs: list[dict]) -> tuple[list[dict], list[dict]]:
    worker = Stage3Worker.__new__(Stage3Worker)
    worker.SIMILARITY_THRESHOLD = 0.3
    worker.semaphore = asyncio.Semaphore(4)
    worker._fingerprints = FingerprintStore(0.3, redis_client=None)
    unique = await worker._deduplicate_documents(docs)
    summaries = []
    for doc in unique:
        summaries.append(await worker._summarize_document(doc))
    return unique, summaries


def _stage4(queue: list[dict], fallback: list[dict]) -> Stage4Worker:
    worker = Stage4Worker.__new__(Stage4Worker)
    worker.analysis_fallback = True
    worker.delta = object()

    def _read(table: str, columns: list[str] | None = None) -> list[dict]:
        rows = {QUEUE_TABLE: queue, ANALYSIS_TABLE: fallback}.get(table, [])
        if columns:
            return [{key: row.get(key) for key in columns} for row in rows]
        return rows

    worker._read = _read
    return worker
