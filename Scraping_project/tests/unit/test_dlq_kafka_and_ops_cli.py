"""Kafka produce failures are dead-lettered, and the DLQ has an ops CLI (#162)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import pipelines as p
from src.utils import dead_letter_queue as dq
from src.utils.dead_letter_queue import DeadLetterQueue, default_dlq_path, main
from src.utils.delta import DeltaHelper

SPIDER = SimpleNamespace(name="scout")
ROOT = Path(__file__).resolve().parents[2]


class DeadBroker:
    """Producer whose every produce() raises, as during a broker outage."""

    def __init__(self):
        self.calls = 0

    def produce(self, *args, **kwargs):
        self.calls += 1
        raise BufferError("Local: Queue full")

    def poll(self, timeout=0):
        return 0

    def flush(self, timeout=None):
        return 0


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("DLQ_PATH", raising=False)
    monkeypatch.delenv("KAFKA_DLQ_ENABLED", raising=False)


def _kafka(tmp_path):
    k = p.KafkaPipeline("broker:9092", "items", spill_dir=tmp_path / "spill", produce_retries=2, retry_backoff=0.0)
    k.producer = DeadBroker()
    return k


# --- Kafka -> DLQ ------------------------------------------------------------


def test_exhausted_kafka_produce_is_dead_lettered_and_spilled(tmp_path):
    k = _kafka(tmp_path)
    item = {"url": "https://uconn.edu/k1", "url_hash": "hk1", "title": "K"}
    assert k.process_item(item, SPIDER) is item
    assert (tmp_path / "spill" / "items.jsonl").exists()  # spill (replay file) unchanged
    entries = DeadLetterQueue(tmp_path / "dlq").list_failed(stage="kafka")
    assert len(entries) == 1
    e = entries[0]
    assert e["url"] == "https://uconn.edu/k1" and e["url_hash"] == "hk1"
    assert e["item"]["title"] == "K"
    assert e["context"]["topic"] == "items" and e["context"]["reason"] == "produce_error"
    assert "Queue full" in e["error"]["message"]


def test_dlq_path_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("DLQ_PATH", str(tmp_path / "ops_dlq"))
    _kafka(tmp_path).process_item({"url": "https://uconn.edu/k2"}, SPIDER)
    assert len(DeadLetterQueue(tmp_path / "ops_dlq").list_failed(stage="kafka")) == 1
    assert default_dlq_path() == tmp_path / "ops_dlq"


def test_kafka_dlq_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("KAFKA_DLQ_ENABLED", "0")
    _kafka(tmp_path).process_item({"url": "https://uconn.edu/k3"}, SPIDER)
    assert not (tmp_path / "dlq").exists()
    assert (tmp_path / "spill" / "items.jsonl").exists()


def test_dlq_failure_never_breaks_the_spill(tmp_path, monkeypatch):
    def boom(self, *a, **kw):
        raise OSError("dlq disk gone")

    monkeypatch.setattr(DeadLetterQueue, "add", boom)
    item = {"url": "https://uconn.edu/k4"}
    assert _kafka(tmp_path).process_item(item, SPIDER) is item
    assert (tmp_path / "spill" / "items.jsonl").exists()


def test_non_json_payload_is_kept_raw(tmp_path):
    k = _kafka(tmp_path)
    k._spill(b"\xff not json", "produce_error", "x")
    (entry,) = DeadLetterQueue(tmp_path / "dlq").list_failed(stage="kafka")
    assert "not json" in entry["item"]["raw"]


# --- ops CLI -----------------------------------------------------------------


def _seed(tmp_path):
    d = DeadLetterQueue(tmp_path / "dlq")
    s2 = d.add({"url": "https://uconn.edu/s2", "url_hash": "hs2", "_retry_count": 3},
               RuntimeError("timeout (error_code=0)"), stage="stage2", context={"max_retries": 3})
    kf = d.add({"url": "https://uconn.edu/kf", "title": "T"}, RuntimeError("Kafka produce_error"), stage="kafka")
    return d, s2, kf


def test_cli_list_stats_show(tmp_path, capsys):
    _, s2, kf = _seed(tmp_path)
    assert main(["--path", str(tmp_path / "dlq"), "list"]) == 0
    out = capsys.readouterr().out
    assert s2 in out and kf in out and "2 entries" in out
    assert main(["--path", str(tmp_path / "dlq"), "list", "--stage", "kafka", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [e["id"] for e in listed] == [kf]
    assert main(["--path", str(tmp_path / "dlq"), "stats"]) == 0
    assert json.loads(capsys.readouterr().out)
    assert main(["--path", str(tmp_path / "dlq"), "show", s2]) == 0
    assert json.loads(capsys.readouterr().out)["url"] == "https://uconn.edu/s2"
    assert main(["--path", str(tmp_path / "dlq"), "show", "nope"]) == 1


def test_cli_replay_requeues_stage2_url_in_a_real_lake(tmp_path, capsys):
    d, s2, _ = _seed(tmp_path)
    lake = DeltaHelper(tmp_path / "lake")
    lake.write("stage2_queue", [{"url": "https://uconn.edu/s2", "url_hash": "hs2",
                                 "enqueued_at": "2026-10-01T00:00:00+00:00", "status": "failed"}],
               mode="append", async_write=False)
    assert main(["--path", str(tmp_path / "dlq"), "replay", s2], delta=lake) == 0
    assert "requeued https://uconn.edu/s2 (retry 4)" in capsys.readouterr().out
    rows = [r for r in lake.read("stage2_queue") if r["url_hash"] == "hs2"]
    assert len(rows) == 1 and rows[0]["status"] == "pending"  # upserted, not duplicated
    assert [e["id"] for e in d.list_failed(stage="stage2")] == []  # moved to resolved/
    assert (tmp_path / "dlq" / "resolved" / f"{s2}.json").exists()
    lake.manager.shutdown()


def test_cli_replay_dry_run_changes_nothing(tmp_path, capsys):
    d, s2, _ = _seed(tmp_path)

    class NoLake:
        def merge_into(self, *a, **kw):
            raise AssertionError("dry run must not write")

    assert main(["--path", str(tmp_path / "dlq"), "replay", "--stage", "stage2", "--dry-run"], delta=NoLake()) == 0
    assert "would requeue https://uconn.edu/s2" in capsys.readouterr().out
    assert [e["id"] for e in d.list_failed(stage="stage2")] == [s2]


def test_cli_replay_kafka_prints_item_with_incremented_retry(tmp_path, capsys):
    d, _, kf = _seed(tmp_path)
    assert main(["--path", str(tmp_path / "dlq"), "replay", kf]) == 0
    item = json.loads(capsys.readouterr().out.strip())
    assert item["url"] == "https://uconn.edu/kf" and item["_retry_count"] == 1 and item["_dlq_entry_id"] == kf
    assert [e["id"] for e in d.list_failed(stage="kafka")] == [kf]  # resolve after re-delivery
    assert main(["--path", str(tmp_path / "dlq"), "resolve", kf]) == 0
    assert d.list_failed(stage="kafka") == []


def test_cli_replay_reports_failed_requeue(tmp_path, capsys):
    d, s2, _ = _seed(tmp_path)

    class BrokenLake:
        def merge_into(self, *a, **kw):
            return -1

    assert main(["--path", str(tmp_path / "dlq"), "replay", s2], delta=BrokenLake()) == 1
    assert [e["id"] for e in d.list_failed(stage="stage2")] == [s2]  # left open


def test_requeue_computes_missing_url_hash():
    seen = {}

    class Lake:
        def merge_into(self, table, rows, key, cols):
            seen.update(rows[0], table=table)
            return 1

    assert dq.requeue_stage2({"url": "https://uconn.edu/nohash", "url_hash": "unknown"}, Lake())
    assert seen["table"] == "stage2_queue" and seen["status"] == "pending" and len(seen["url_hash"]) >= 16


def test_module_entrypoint_runs(tmp_path):
    _seed(tmp_path)
    out = subprocess.run([sys.executable, "-m", "src.utils.dead_letter_queue", "--path", str(tmp_path / "dlq"),
                          "list", "--stage", "stage2"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "1 entry" in out.stdout
