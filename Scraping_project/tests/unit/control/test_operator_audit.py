"""Append-only operator audit schema, JSONL log, and Delta mirror (#657)."""
from __future__ import annotations

import json

import pytest

from src.control.operator_audit import (
    AUDIT_EVENT_FIELDS,
    AUDIT_EVENT_SCHEMA,
    CONTROL_ACTIONS,
    AuditError,
    OperatorAuditLog,
    append_delta,
    normalize_event,
)
from src.core.schemas import OPERATOR_AUDIT_SCHEMA, get_schema


def test_schema_fields_match_delta_and_json_schema():
    assert AUDIT_EVENT_FIELDS == ("actor", "action", "ts", "job_id")
    assert list(OPERATOR_AUDIT_SCHEMA.names) == list(AUDIT_EVENT_FIELDS)
    assert get_schema("operator_audit") is OPERATOR_AUDIT_SCHEMA
    assert AUDIT_EVENT_SCHEMA["required"] == list(AUDIT_EVENT_FIELDS)
    assert AUDIT_EVENT_SCHEMA["additionalProperties"] is False
    assert "pause_stage2" in CONTROL_ACTIONS
    assert set(AUDIT_EVENT_SCHEMA["properties"]["action"]["enum"]) == set(CONTROL_ACTIONS)


def test_normalize_rejects_unknown_action_and_control_chars():
    with pytest.raises(AuditError, match="unknown action"):
        normalize_event(actor="operator", action="drop_database")
    with pytest.raises(AuditError, match="control"):
        normalize_event(actor="operator", action="pause", job_id="job\n1")
    with pytest.raises(AuditError, match="actor"):
        normalize_event(actor="", action="pause")


def test_jsonl_is_append_only_and_newest_first(tmp_path):
    path = tmp_path / "events.jsonl"
    log = OperatorAuditLog(path)
    with pytest.raises(AuditError):
        log.append(actor="operator", action="nope")
    assert not path.exists()

    first = log.append(actor="ada", action="pause_stage2", job_id="job-1", ts="2026-10-14T12:00:00Z")
    raw_first = path.read_bytes()
    assert raw_first.endswith(b"\n")
    assert json.loads(raw_first)["action"] == "pause_stage2"
    second = log.append(actor="ada", action="resume_stage2", job_id="job-1", ts="2026-10-14T12:01:00Z")
    raw_both = path.read_bytes()
    assert raw_both.startswith(raw_first)
    assert raw_both.count(b"\n") == 2

    assert log.read_last(1) == [second]
    assert log.read_last(10) == [second, first]
    assert path.read_bytes() == raw_both  # a read must not rewrite the file
    assert log.read_last(0) == []


def test_read_skips_a_torn_line(tmp_path):
    path = tmp_path / "events.jsonl"
    log = OperatorAuditLog(path)
    good = log.append(actor="ada", action="stop", job_id="", ts="2026-10-14T12:00:00Z")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not-json\n")
    assert log.read_last(10) == [good]


def test_delta_append_keeps_both_rows(tmp_path):
    deltalake = pytest.importorskip("deltalake")
    event = normalize_event(actor="ada", action="pause_stage2", job_id="job-9", ts="2026-10-14T12:00:00Z")
    later = normalize_event(actor="bea", action="resume_stage2", job_id="job-9", ts="2026-10-14T12:05:00Z")
    table = tmp_path / "operator_audit"
    append_delta(event, table)
    append_delta(later, table)
    rows = deltalake.DeltaTable(str(table)).to_pyarrow_table().to_pylist()
    rows = sorted(rows, key=lambda r: r["ts"])
    assert [(r["actor"], r["action"], r["job_id"]) for r in rows] == [
        ("ada", "pause_stage2", "job-9"),
        ("bea", "resume_stage2", "job-9"),
    ]
    assert rows[0]["ts"] == "2026-10-14T12:00:00Z"
