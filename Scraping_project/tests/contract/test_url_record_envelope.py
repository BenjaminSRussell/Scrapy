"""Stage 1 -> Stage 2 URL record envelope contract (#667).

The producer (ScoutSpider._queue_for_stage2) and the consumer (Stage2Worker's
stage2_queue gate) are both checked against the versioned fixture in
tests/fixtures/stage1_stage2/. Offline: Delta is faked and Redis is fakeredis.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fakeredis
import pytest

from src.core.url_record_envelope import (
    REQUIRED_URL_RECORD_FIELDS,
    URL_RECORD_ENVELOPE_VERSION,
    UrlRecordEnvelopeError,
    validate_url_record_envelope,
)
from src.otel_tracing import get_crawl_job_id
from src.stage1.scout_spider import ScoutSpider
from src.stage2 import stage2_worker as s2

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "stage1_stage2" / "url_record_envelope_v1.json"

pytestmark = pytest.mark.contract


def _fixture_records() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def scout():
    redis_helper = SimpleNamespace(client=fakeredis.FakeRedis())
    with patch("src.stage1.scout_spider.get_delta_manager"), \
            patch("src.stage1.experimental.base_spider.get_redis", return_value=redis_helper):
        return ScoutSpider(allowed_domains=["uconn.edu"])


def test_fixture_is_pinned_to_current_version():
    records = _fixture_records()
    assert records and all(r["envelope_version"] == URL_RECORD_ENVELOPE_VERSION for r in records)
    for record in records:
        validate_url_record_envelope(record)


def test_producer_emits_contract_envelope(scout):
    row = scout._queue_for_stage2("https://uconn.edu/admissions", "https://uconn.edu/", "html")

    validate_url_record_envelope(row)
    assert row["envelope_version"] == URL_RECORD_ENVELOPE_VERSION
    assert row["url"] == "https://uconn.edu/admissions"
    assert row["crawl_job_id"] == get_crawl_job_id()
    assert row["target_stage"] == "stage2"
    for field in REQUIRED_URL_RECORD_FIELDS:
        assert field in row


@pytest.mark.parametrize("field", sorted(set(REQUIRED_URL_RECORD_FIELDS) - {"envelope_version"}))
def test_missing_required_field_is_rejected(field):
    record = copy.deepcopy(_fixture_records()[0])
    del record[field]
    with pytest.raises(UrlRecordEnvelopeError, match=f"{field} must be"):
        validate_url_record_envelope(record)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [("url", 42), ("url", "   "), ("status", None), ("crawl_job_id", ""), ("priority", "2"), ("discovery_method", 3)],
)
def test_wrong_type_or_blank_value_is_rejected(field, bad_value):
    record = copy.deepcopy(_fixture_records()[0])
    record[field] = bad_value
    with pytest.raises(UrlRecordEnvelopeError):
        validate_url_record_envelope(record)


def test_optional_fields_accept_null_and_unknown_fields_are_ignored():
    records = _fixture_records()
    assert records[1]["parent_url"] is None
    assert "x_future_field" in records[1]
    validate_url_record_envelope(records[1])


def test_discovery_method_optional_field_accepts_str_and_null():
    record = copy.deepcopy(_fixture_records()[0])
    record["discovery_method"] = "json_ld"
    validate_url_record_envelope(record)
    record["discovery_method"] = None
    validate_url_record_envelope(record)


@pytest.mark.parametrize("version", [2, 0, "1", None])
def test_mismatched_version_fails_clearly_and_deterministically(version):
    record = {**_fixture_records()[0], "envelope_version": version}
    messages = []
    for _ in range(2):
        with pytest.raises(UrlRecordEnvelopeError) as exc:
            validate_url_record_envelope(record)
        messages.append(str(exc.value))
    assert messages[0] == messages[1]
    assert messages[0] == (
        f"unsupported envelope_version {version!r}; expected {URL_RECORD_ENVELOPE_VERSION}"
    )


class _FakeDelta:
    def __init__(self, queue):
        self.queue = queue
        self.last_read_error = None

    def read_table(self, name, **kwargs):
        return list(self.queue) if name == "stage2_queue" else []

    def write(self, table, rows, mode="append", async_write=True):
        return True

    def merge_into(self, table, rows, merge_key, update_columns):
        return len(rows)


def _worker(delta):
    w = s2.Stage2Worker.__new__(s2.Stage2Worker)
    w.max_concurrent, w.batch_size = 2, 10
    w.semaphore = asyncio.Semaphore(2)
    w.delta = delta
    w.postgres = None
    w.max_retries = 3
    w._dlq = None
    return w


def test_stage2_consumer_analyzes_contract_rows_and_skips_mismatched_version(monkeypatch):
    v1 = _fixture_records()
    legacy = {"url": "https://uconn.edu/legacy", "url_hash": "h-legacy", "status": "pending"}
    mismatched = {**v1[0], "envelope_version": 2, "url": "https://uconn.edu/v2-only"}
    worker = _worker(_FakeDelta([*v1, legacy, mismatched]))
    analyzed = []

    async def analyze(record):
        analyzed.append(record["url"])
        return {"url": record["url"], "url_hash": record["url_hash"], "has_error": False, "is_low_quality": False}

    async def update(urls, table_name="stage2_queue", status="completed"):
        return None

    monkeypatch.setattr(worker, "_analyze_url", analyze)
    monkeypatch.setattr(worker, "_update_queue_status", update)

    asyncio.run(worker._run_traced())

    assert sorted(analyzed) == sorted([v1[0]["url"], v1[1]["url"], legacy["url"]])
    assert mismatched["url"] not in analyzed
