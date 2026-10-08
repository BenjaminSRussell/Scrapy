"""#258: DeadLetterQueue round trip, stats, resolve, cleanup and corruption safety.

The DLQ is file-backed (one JSON per entry under DLQ_PATH), so these tests use
``tmp_path`` rather than Redis; nothing touches the network.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.core.exceptions import PipelineException
from src.utils import dead_letter_queue as dlq_mod
from src.utils.dead_letter_queue import DeadLetterQueue, default_dlq_path


@pytest.fixture
def dlq(tmp_path):
    return DeadLetterQueue(tmp_path / "dlq")


def _boom(message="boom"):
    try:
        raise ValueError(message)
    except ValueError as e:
        return e


def test_round_trip_preserves_the_item_exactly(dlq):
    item = {
        "url": "https://uconn.edu/a",
        "url_hash": "abc",
        "nested": {"list": [1, 2.5, None, True], "unicode": "café ✓"},
        "_retry_count": 2,
    }
    entry_id = dlq.add(item, _boom(), stage="stage2", context={"worker_id": "w1"})
    assert entry_id.startswith("stage2_")

    [entry] = dlq.list_failed()
    assert entry["id"] == entry_id
    assert entry["item"] == item
    assert entry["stage"] == "stage2" and entry["context"] == {"worker_id": "w1"}
    assert entry["retry_count"] == 2 and entry["url_hash"] == "abc"
    assert entry["error"]["type"] == "ValueError" and entry["error"]["message"] == "boom"

    replayed = dlq.replay(entry_id)
    assert replayed["_retry_count"] == 3
    assert replayed["_dlq_entry_id"] == entry_id
    assert {k: v for k, v in replayed.items() if not k.startswith("_")} == {
        k: v for k, v in item.items() if not k.startswith("_")
    }


def test_traceback_is_the_errors_own_even_outside_the_except_block(dlq):
    error = _boom()  # add() is called after the except block has ended
    entry_id = dlq.add({"url": "u"}, error, stage="stage3")
    tb = dlq.list_failed()[0]["error"]["traceback"]
    assert "NoneType: None" not in tb
    assert "_boom" in tb and "ValueError: boom" in tb
    assert entry_id


def test_unserializable_fields_no_longer_lose_the_item(dlq):
    # datetime/bytes/Decimal/set used to abort json.dump -> add() returned "" and the item was lost.
    item = {"url": "u", "fetched_at": datetime(2026, 1, 2, 3, 4), "raw": b"\x00\xff", "n": Decimal("1.5"), "tags": {"a"}}
    entry_id = dlq.add(item, _boom(), stage="stage2")
    assert entry_id
    [entry] = dlq.list_failed()
    assert entry["item"]["fetched_at"].startswith("2026-01-02")
    assert entry["item"]["n"] == "1.5"


def test_writes_are_atomic_no_partial_entries(dlq, monkeypatch):
    real_replace = os.replace

    def crash(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(dlq_mod.os, "replace", crash)
    assert dlq.add({"url": "u"}, _boom(), stage="stage2") == ""
    monkeypatch.setattr(dlq_mod.os, "replace", real_replace)

    assert list(dlq.base_path.glob("*.json")) == []  # nothing half-written to list
    assert dlq.list_failed() == []


def test_pipeline_exception_details_are_kept(dlq):
    from src.core.exceptions import ErrorCategory, ErrorSeverity

    err = PipelineException("bad row", category=ErrorCategory.VALIDATION, severity=ErrorSeverity.HIGH, retryable=False)
    dlq.add({"url": "u"}, err, stage="stage2")
    error = dlq.list_failed()[0]["error"]
    assert error["category"] == ErrorCategory.VALIDATION.value
    assert error["severity"] == ErrorSeverity.HIGH.value
    assert error["retryable"] is False


def test_list_filters_sorts_newest_first_and_limits(dlq):
    ids = []
    for stage in ("stage2", "stage3", "stage2"):
        ids.append(dlq.add({"url": stage}, _boom(), stage=stage))
    # Make the order deterministic regardless of clock resolution.
    for i, entry_id in enumerate(ids):
        path = dlq.base_path / f"{entry_id}.json"
        data = json.loads(path.read_text())
        data["timestamp"] = (datetime(2026, 1, 1) + timedelta(minutes=i)).isoformat()
        path.write_text(json.dumps(data))

    assert [e["id"] for e in dlq.list_failed()] == ids[::-1]
    assert [e["id"] for e in dlq.list_failed(stage="stage2")] == [ids[2], ids[0]]
    assert [e["id"] for e in dlq.list_failed(limit=1)] == [ids[2]]


def test_corrupt_files_are_skipped_not_fatal(dlq):
    dlq.add({"url": "ok"}, _boom(), stage="stage2")
    (dlq.base_path / "broken.json").write_text("{not json")
    assert [e["item"]["url"] for e in dlq.list_failed()] == ["ok"]
    assert dlq.get_stats()["total_failed"] == 1


def test_stats_group_by_stage_and_error_type(dlq):
    dlq.add({"url": "a"}, _boom(), stage="stage2")
    dlq.add({"url": "b"}, KeyError("k"), stage="stage2")
    dlq.add({"url": "c"}, _boom(), stage="stage4")
    stats = dlq.get_stats()
    assert stats["total_failed"] == 3
    assert stats["by_stage"] == {"stage2": 2, "stage4": 1}
    assert stats["by_error_type"] == {"ValueError": 2, "KeyError": 1}
    assert stats["oldest_failure"] <= stats["newest_failure"]


def test_resolve_moves_entries_out_of_the_active_set(dlq):
    ok = dlq.add({"url": "a"}, _boom(), stage="stage2")
    bad = dlq.add({"url": "b"}, _boom(), stage="stage2")
    assert dlq.resolve(ok, success=True)
    assert dlq.resolve(bad, success=False)
    assert dlq.list_failed() == []
    assert (dlq.base_path / "resolved" / f"{ok}.json").exists()
    assert (dlq.base_path / "failed" / f"{bad}.json").exists()
    assert dlq.resolve("missing") is False
    assert dlq.replay("missing") is None


def test_cleanup_old_removes_only_expired_entries(dlq):
    old = dlq.add({"url": "old"}, _boom(), stage="stage2")
    new = dlq.add({"url": "new"}, _boom(), stage="stage2")
    path = dlq.base_path / f"{old}.json"
    data = json.loads(path.read_text())
    data["timestamp"] = (datetime.now() - timedelta(days=40)).isoformat()
    path.write_text(json.dumps(data))

    assert dlq.cleanup_old(days=30) == 1
    assert [e["id"] for e in dlq.list_failed()] == [new]


def test_default_path_honours_dlq_path_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DLQ_PATH", str(tmp_path / "shared"))
    assert default_dlq_path() == tmp_path / "shared"
    monkeypatch.delenv("DLQ_PATH")
    assert default_dlq_path(tmp_path / "fb") == tmp_path / "fb"
