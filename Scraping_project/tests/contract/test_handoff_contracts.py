"""Versioned stage hand-off contracts, replayed from recorded fixtures.

#623 schema_version on lake rows, #667 stage1->stage2, #668 stage2->stage3,
#659 stage2->stage4 docs + Stage 4 chunks, #686 offline replay of recorded files.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core import contracts as c

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "handoffs"


def _cases(name):
    return [json.loads(line) for line in (FIXTURES / f"{name}.jsonl").read_text(encoding="utf-8").splitlines() if line]


ALL = [(name, case) for name in c.CONTRACTS for case in _cases(name)]


def test_every_boundary_has_recorded_valid_legacy_and_future_cases():
    for name in c.CONTRACTS:
        cases = {k["case"] for k in _cases(name)}
        assert any(k.startswith("v1_") for k in cases), name
        assert "future_version" in cases, name
        # field-level rejects are recorded at the current version, not only version mismatches
        assert any(k["expect"] == "reject" and k["record"].get("schema_version") == 1 for k in _cases(name)), name


@pytest.mark.parametrize("name,case", ALL, ids=[f"{n}:{k['case']}" for n, k in ALL])
def test_replay_fixture(name, case):
    contract = c.CONTRACTS[name]
    record = case["record"]
    errors = contract.errors(record)
    assert errors == contract.errors(dict(record))  # deterministic
    if case["expect"] == "valid":
        assert errors == []
        assert c.check(record, contract) is record
    else:
        assert errors, case["case"]
        with pytest.raises(c.ContractError) as info:
            c.check(record, contract)
        assert case["error"] in str(info.value)
        assert info.value.contract == name


def test_split_valid_and_reject_metric():
    cases = _cases("stage1_stage2")
    valid, rejected = c.split_valid([k["record"] for k in cases], c.STAGE1_STAGE2)
    assert len(valid) == sum(k["expect"] == "valid" for k in cases)
    assert len(rejected) == sum(k["expect"] == "reject" for k in cases)
    if c.CONTRACT_REJECTS is not None:
        before = c.CONTRACT_REJECTS.labels(contract="stage1_stage2", reason="version")._value.get()
    log = MagicMock()
    c.record_rejects(rejected, log)
    assert log.warning.call_count == len(rejected)
    if c.CONTRACT_REJECTS is not None:
        versions = sum("schema_version" in " ".join(e.errors) for _, e in rejected)
        assert c.CONTRACT_REJECTS.labels(contract="stage1_stage2", reason="version")._value.get() == before + versions


def test_stamp_does_not_overwrite_and_non_mapping_is_rejected():
    assert c.stamp({"url": "u"}, c.STAGE1_STAGE2)["schema_version"] == 1
    assert c.stamp({"schema_version": 1}, c.STAGE2_STAGE3)["schema_version"] == 1
    assert c.STAGE1_STAGE2.errors(["not", "a", "row"]) == ["expected a mapping, got list"]


def test_factories_satisfy_contracts():
    from tests import factories as f

    assert c.STAGE1_STAGE2.errors(f.url_record()) == []
    assert c.STAGE2_STAGE3.errors(f.stage2_record()) == []
    assert c.STAGE4_CHUNK.errors(f.stage4_chunk()) == []


# --- producers emit current-version rows ------------------------------------


def test_scout_queue_item_is_stamped_by_queue_pipeline(tmp_path, monkeypatch):
    from src import pipelines as p
    from src.stage1.scout_spider import ScoutSpider

    item = ScoutSpider._queue_for_stage2(SimpleNamespace(), "https://www.uconn.edu/a/", "https://www.uconn.edu/", "html")
    queue = p.QueueItemPipeline.__new__(p.QueueItemPipeline)
    class _Batch(list):
        add = list.append

    queue.stage2_queue_batch, queue.js_queue_batch, queue.items_processed = _Batch(), _Batch(), 0
    queue.process_item(item, SimpleNamespace(name="scout"))
    (row,) = queue.stage2_queue_batch
    assert row["schema_version"] == c.STAGE1_STAGE2.version
    assert c.STAGE1_STAGE2.errors(row) == []
    assert row["url_hash"]


def test_seed_manager_enqueue_is_stamped():
    from src.lakehouse.seed_manager import SeedManager

    lake = MagicMock()
    lake.read.return_value = []
    SeedManager(lake).add_urls_to_seeds(["https://www.uconn.edu/x"], "https://www.uconn.edu/", "manual", enqueue_stage2=True)
    calls = [k for k in lake.merge_into.call_args_list if (k.args or [None])[0] == "stage2_queue"]
    assert calls, lake.merge_into.call_args_list
    rows = calls[0].args[1]
    assert rows and all(r["schema_version"] == 1 and not c.STAGE1_STAGE2.errors(r) for r in rows)


def _stage2_worker(delta):
    from src.stage2.stage2_worker import Stage2Worker

    w = Stage2Worker.__new__(Stage2Worker)
    w.delta = delta
    return w


def test_stage2_analysis_upsert_and_stage4_routes_are_stamped():
    from tests import factories as f

    delta = MagicMock()
    delta.merge_into.return_value = 1
    w = _stage2_worker(delta)
    assert asyncio.run(w._write_analysis([f.stage2_record()]))
    rows = delta.merge_into.call_args.args[1]
    assert rows[0]["schema_version"] == 1 and c.STAGE2_STAGE3.errors(rows[0]) == []

    asyncio.run(w._route_to_stage4("https://www.uconn.edu/h", "hh", "text " * 10, 60000, 400000))
    w._route_pdf_to_stage4("https://www.uconn.edu/c.pdf", "pp")
    routed = [k.args[1][0] for k in delta.write.call_args_list if k.args[0] == "stage4_large_docs"]
    assert len(routed) == 2
    assert all(r["schema_version"] == 1 and c.STAGE2_STAGE4.errors(r) == [] for r in routed)


# --- consumers reject before work ---------------------------------------------


def test_stage2_consumer_skips_unsupported_rows_and_leaves_them_pending(tmp_path):
    from src.lakehouse.lakehouse_manager import LakehouseManager
    from src.stage2.stage2_worker import Stage2Worker

    cases = {k["case"]: k["record"] for k in _cases("stage1_stage2")}
    rows = [
        dict(cases["v1_scout_row"], url="https://www.uconn.edu/v1", url_hash="hv1"),
        dict(cases["legacy_seed_manager_row"], url="https://www.uconn.edu/legacy", url_hash="hleg"),
        dict(cases["future_version"], url="https://www.uconn.edu/v2", url_hash="hv2"),
    ]
    lake = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    assert lake._write_sync("stage2_queue", rows, "append")
    seen = []

    class _Recording(Stage2Worker):
        def __init__(self):
            super().__init__(max_concurrent=2, batch_size=10)
            self.delta = lake
            self.postgres = None

        async def _analyze_url(self, record):
            seen.append(record["url"])
            return {"url": record["url"], "url_hash": record["url_hash"], "_deferred": True}

    try:
        asyncio.run(_Recording().run())
    finally:
        lake.shutdown_event.set()
    assert sorted(seen) == ["https://www.uconn.edu/legacy", "https://www.uconn.edu/v1"]
    assert {r["url_hash"]: r["status"] for r in lake.read("stage2_queue")}["hv2"] == "pending"


@pytest.fixture
def stage3(monkeypatch):
    from src.stage3.stage3_worker import Stage3Worker
    from src.utils import graceful_shutdown as gs

    w = Stage3Worker(max_concurrent=2, batch_size=4)
    w.postgres = MagicMock()
    for name in ("init_tracing", "record_performance", "record_error"):
        monkeypatch.setattr(f"src.stage3.stage3_worker.{name}", MagicMock())
    monkeypatch.setattr("src.stage3.stage3_worker.ensure_crawl_job_id", lambda: "job")
    monkeypatch.setattr("src.stage3.stage3_worker.start_span", MagicMock())
    gs.reset_for_tests()
    yield w
    gs.reset_for_tests()


def test_stage3_consumer_summarises_only_contract_valid_rows(stage3):
    topics = iter(["chemistry", "basketball", "admissions", "library", "housing", "parking", "nursing", "music"])
    docs, expected = [], set()
    for k in _cases("stage2_stage3"):
        if "text_content" not in k["record"]:
            continue  # Stage 3's quality filter drops these before the contract
        t = next(topics, None)
        if t is None:
            break
        row = dict(k["record"], url=f"https://www.uconn.edu/{k['case']}")
        if row.get("url_hash") is not None:
            row["url_hash"] = f"h-{k['case']}"
        row["text_content"] = " ".join(f"{t}{j}" for j in range(60)) + f". {t.title()} second. Third {t}."
        docs.append(row)
        if k["expect"] == "valid":
            expected.add(row["url"])
    written = []

    class _D:
        def read(self, table, **_):
            if table == "stage2_page_analysis":
                return list(docs)
            raise FileNotFoundError(table)

        def write(self, table, rows, **_):
            written.extend(rows)
            return True

    stage3.delta = _D()
    asyncio.run(stage3.run())
    assert {r["url"] for r in written} == expected
    assert expected and len(expected) < len(docs)


def test_stage4_consumer_rejects_future_version_before_fetch(tmp_path):
    from src.stage4.stage4_worker import QUEUE_TABLE, Stage4Worker
    from src.utils.delta import DeltaHelper

    cases = {k["case"]: k["record"] for k in _cases("stage2_stage4")}
    w = Stage4Worker.__new__(Stage4Worker)
    w.delta = DeltaHelper(base_path=tmp_path / "lake")
    fetched = []
    w.processor = SimpleNamespace(
        _fetch_content=lambda url, is_pdf=False: (fetched.append(url), ("x " * 50, "html"))[1],
        process_large_document=lambda url, text: "summary",
    )
    rows = [cases["v1_html_route"], cases["legacy_route"] | {"url": "https://www.uconn.edu/legacy"},
            cases["future_version"] | {"url": "https://www.uconn.edu/v2"}]
    w.delta.write(QUEUE_TABLE, [dict(r, is_pdf=bool(r.get("is_pdf"))) for r in rows], mode="append", async_write=False)
    asyncio.run(w._run_traced())
    assert "https://www.uconn.edu/v2" not in fetched
    assert set(fetched) == {"https://www.uconn.edu/handbook", "https://www.uconn.edu/legacy"}
    status = {r["url"]: r["status"] for r in w.delta.read(QUEUE_TABLE)}
    assert status["https://www.uconn.edu/v2"] == "pending"


# --- Stage 4 chunks -----------------------------------------------------------


def _processor():
    from src.stage4.large_doc_processor import LargeDocProcessor

    p = LargeDocProcessor.__new__(LargeDocProcessor)  # skip get_delta()/httpx/model load
    p.CHUNK_SIZE, p.OVERLAP = 5000, 500  # as set in __init__
    return p


@pytest.mark.parametrize("size_delta", [-1, 0, 1])
def test_chunk_boundaries(size_delta):
    p = _processor()
    text = "a" * (p.CHUNK_SIZE + size_delta)
    chunks = p.chunk_records("https://u/doc", "hdoc", text)
    assert len(chunks) == (1 if size_delta <= 0 else 2)
    assert chunks[0]["start"] == 0 and chunks[-1]["end"] == len(text)


def test_empty_and_whitespace_text_have_no_chunks():
    p = _processor()
    assert p.chunk_records("https://u/doc", "hdoc", "") == []
    assert p.chunk_records("https://u/doc", "hdoc", "   \n ") == []


def test_multi_chunk_ids_order_and_source_survive_round_trip():
    p = _processor()
    text = " ".join(f"Sentence {i} about café naïve 日本 😀." for i in range(600))
    chunks = p.chunk_records("https://www.uconn.edu/handbook", "11aa22bb33cc44dd", text)
    assert len(chunks) > 2
    back = [json.loads(json.dumps(ch, ensure_ascii=False)) for ch in chunks]
    assert back == chunks
    for i, ch in enumerate(back):
        c.check(ch, c.STAGE4_CHUNK)
        assert ch["chunk_index"] == i and ch["total"] == len(chunks)
        assert ch["chunk_id"] == f"11aa22bb33cc44dd:{i:05d}"
        assert ch["url"] == "https://www.uconn.edu/handbook" and ch["url_hash"] == "11aa22bb33cc44dd"
        assert ch["text"] == text[ch["start"]:ch["end"]]
    assert [ch["start"] for ch in back] == sorted(ch["start"] for ch in back)
    assert [ch["text"].strip() for ch in back] == p._split_into_chunks(text)


def test_future_version_chunk_fails_before_stage4_work():
    record = next(k["record"] for k in _cases("stage4_chunk") if k["case"] == "future_version")
    with pytest.raises(c.ContractError, match="schema_version 2 is not supported"):
        c.check(record, c.STAGE4_CHUNK)


# --- lake: the new column evolves existing tables ---------------------------


def test_schema_version_column_merges_into_legacy_table(tmp_path):
    from src.lakehouse.lakehouse_manager import LakehouseManager

    lake = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    try:
        assert lake._write_sync("stage2_queue", [{"url": "https://u/old", "url_hash": "old", "status": "pending"}], "append")
        new = c.stamp({"url": "https://u/new", "url_hash": "new", "status": "pending"}, c.STAGE1_STAGE2)
        assert lake._write_sync("stage2_queue", [new], "append")
        lake.merge_into("stage2_queue", [c.stamp({"url": "https://u/m", "url_hash": "m", "status": "pending"}, c.STAGE1_STAGE2)],
                        "url_hash", ["url", "status"])
        rows = {r["url_hash"]: r for r in lake.read("stage2_queue")}
    finally:
        lake.shutdown_event.set()
    assert rows["old"].get("schema_version") is None and rows["new"]["schema_version"] == 1
    valid, rejected = c.split_valid(rows.values(), c.STAGE1_STAGE2)
    assert rejected == [] and len(valid) == len(rows)
