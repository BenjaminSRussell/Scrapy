"""Stage 3 → Stage 4 chunk handoff contract (#659).

Fixtures: ``tests/fixtures/chunk_handoff/``. Stage 3 serializes; Stage 4
deserializes. An unknown ``schema_version`` raises before per-chunk work.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.stage3.chunk_handoff import (
    CHUNK_FIELDS,
    MAX_CHUNK_CHARS,
    SCHEMA_VERSION,
    make_chunk,
    serialize_handoff,
)
from src.stage3.chunk_handoff import (
    ChunkHandoffError as Stage3HandoffError,
)
from src.stage4.chunk_handoff import SCHEMA_VERSION as STAGE4_SCHEMA_VERSION
from src.stage4.chunk_handoff import (
    ChunkHandoffError,
    begin_stage4_work,
    deserialize_handoff,
)

pytestmark = [pytest.mark.contract, pytest.mark.stage3, pytest.mark.stage4]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "chunk_handoff"
VALID_FIXTURES = (
    "empty.json",
    "single.json",
    "multi.json",
    "boundary_empty.json",
    "boundary_max.json",
)


def _seen():
    found: list[dict] = []
    return found, found.append


def test_both_stages_lock_the_same_schema_version():
    assert SCHEMA_VERSION == STAGE4_SCHEMA_VERSION == 1
    assert Stage3HandoffError is ChunkHandoffError
    assert MAX_CHUNK_CHARS == 5000
    assert set(CHUNK_FIELDS) == {
        "schema_version",
        "chunk_id",
        "chunk_index",
        "source_url",
        "source_url_hash",
        "text",
        "start",
        "end",
    }


def test_required_chunk_fields_and_types():
    envelope = deserialize_handoff((FIXTURES / "single.json").read_text(encoding="utf-8"))
    assert envelope["chunks"]
    for chunk in envelope["chunks"]:
        assert set(chunk) == set(CHUNK_FIELDS)
        for name, typ in CHUNK_FIELDS.items():
            assert isinstance(chunk[name], typ) and not isinstance(chunk[name], bool)


@pytest.mark.parametrize("name", VALID_FIXTURES)
def test_ids_order_and_source_survive_round_trip(name):
    raw = (FIXTURES / name).read_text(encoding="utf-8")
    envelope = deserialize_handoff(raw)
    again = serialize_handoff(envelope["source_url"], envelope["source_url_hash"], envelope["chunks"])
    assert again == raw
    reloaded = begin_stage4_work(again, lambda _chunk: None)
    assert [c["chunk_id"] for c in reloaded["chunks"]] == [c["chunk_id"] for c in envelope["chunks"]]
    assert [c["chunk_index"] for c in reloaded["chunks"]] == list(range(len(envelope["chunks"])))
    for chunk in reloaded["chunks"]:
        assert chunk["source_url"] == envelope["source_url"]
        assert chunk["source_url_hash"] == envelope["source_url_hash"]


def test_empty_handoff_runs_no_chunk_work():
    seen, record = _seen()
    envelope = begin_stage4_work((FIXTURES / "empty.json").read_text(encoding="utf-8"), record)
    assert envelope["chunks"] == []
    assert seen == []


def test_boundary_empty_text_and_max_text_are_accepted():
    empty_seen, empty_record = _seen()
    empty = begin_stage4_work((FIXTURES / "boundary_empty.json").read_text(encoding="utf-8"), empty_record)
    assert len(empty_seen) == 1
    assert empty_seen[0]["text"] == ""
    assert empty_seen[0]["start"] == empty_seen[0]["end"] == 0
    assert empty["source_url_hash"] == "doc-boundary-empty"

    max_seen, max_record = _seen()
    begin_stage4_work((FIXTURES / "boundary_max.json").read_text(encoding="utf-8"), max_record)
    assert len(max_seen) == 1
    assert len(max_seen[0]["text"]) == MAX_CHUNK_CHARS
    assert max_seen[0]["end"] - max_seen[0]["start"] == MAX_CHUNK_CHARS
    assert max_seen[0]["chunk_id"] == "doc-boundary-max:0"


def test_oversize_chunk_is_rejected_before_work():
    source_url = "https://fixture.example/handbook-oversize"
    source_hash = "doc-oversize"
    text = "x" * (MAX_CHUNK_CHARS + 1)
    chunk = make_chunk(
        source_url=source_url,
        source_url_hash=source_hash,
        chunk_index=0,
        text=text,
        start=0,
        end=len(text),
    )
    seen, record = _seen()
    with pytest.raises(ChunkHandoffError, match="boundary"):
        begin_stage4_work(
            {"schema_version": SCHEMA_VERSION, "source_url": source_url, "source_url_hash": source_hash, "chunks": [chunk]},
            record,
        )
    assert seen == []


def test_invalid_version_fixture_fails_before_work():
    seen, record = _seen()
    with pytest.raises(ChunkHandoffError, match="schema_version"):
        begin_stage4_work((FIXTURES / "invalid_version.json").read_text(encoding="utf-8"), record)
    assert seen == []


@pytest.mark.parametrize("version", [0, 2, 99, "1", True, 1.0, None])
def test_invalid_schema_versions_fail_before_work(version):
    payload = json.loads((FIXTURES / "single.json").read_text(encoding="utf-8"))
    if version is None:
        del payload["schema_version"]
    else:
        payload["schema_version"] = version
    seen, record = _seen()
    with pytest.raises(ChunkHandoffError, match="schema_version"):
        begin_stage4_work(payload, record)
    assert seen == []


def test_chunk_schema_version_mismatch_fails_before_work():
    payload = json.loads((FIXTURES / "multi.json").read_text(encoding="utf-8"))
    payload["chunks"][1]["schema_version"] = 2
    seen, record = _seen()
    with pytest.raises(ChunkHandoffError, match="schema_version"):
        begin_stage4_work(payload, record)
    assert seen == []


def test_out_of_order_chunks_are_rejected():
    payload = json.loads((FIXTURES / "multi.json").read_text(encoding="utf-8"))
    payload["chunks"][0], payload["chunks"][1] = payload["chunks"][1], payload["chunks"][0]
    seen, record = _seen()
    with pytest.raises(ChunkHandoffError, match="chunk_index"):
        begin_stage4_work(payload, record)
    assert seen == []


def test_source_reference_mismatch_is_rejected():
    payload = json.loads((FIXTURES / "single.json").read_text(encoding="utf-8"))
    payload["chunks"][0]["source_url"] = "https://fixture.example/other"
    seen, record = _seen()
    with pytest.raises(ChunkHandoffError, match="source reference"):
        begin_stage4_work(payload, record)
    assert seen == []


def test_multi_chunk_ids_stay_with_their_source():
    envelope = deserialize_handoff((FIXTURES / "multi.json").read_text(encoding="utf-8"))
    assert [c["chunk_id"] for c in envelope["chunks"]] == ["doc-multi:0", "doc-multi:1", "doc-multi:2"]
    assert envelope["source_url"] == "https://fixture.example/handbook-multi"
    assert {c["source_url_hash"] for c in envelope["chunks"]} == {"doc-multi"}
