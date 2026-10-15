"""Stage 1 publisher and stage 2 consumer for the versioned URL envelope (#667)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from src.core.stage2_url_envelope import ENVELOPE_VERSION, OPTIONAL_FIELDS, REQUIRED_FIELDS
from src.stage1.scout_spider import ScoutSpider
from src.stage1.stage2_url_envelope import publish_stage2_url_record
from src.stage2.stage2_url_envelope import Stage2UrlEnvelopeError, consume_stage2_url_record
from src.utils.url_canon import url_hash

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "stage1_stage2_url_envelope_v1.json"
_SSRF_URL = "http://169.254.169.254/latest/meta-data/"


def _load() -> dict[str, Any]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _publish(data: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    kwargs = {name: data[name] for name in OPTIONAL_FIELDS if name in data}
    kwargs.update(overrides)
    extra = {
        key: value
        for key, value in data.items()
        if key not in REQUIRED_FIELDS and key not in OPTIONAL_FIELDS and key not in overrides
    }
    return publish_stage2_url_record(
        url=overrides.get("url", data["url"]),
        crawl_job_id=overrides.get("crawl_job_id", data["crawl_job_id"]),
        job_id=overrides.get("job_id", data["job_id"]),
        extra=extra,
        **kwargs,
    )


class _BoomSession:
    async def get(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("stage2 fetched before the envelope check finished")


def test_fixture_is_the_version_1_envelope():
    data = _load()
    assert data["envelope_version"] == ENVELOPE_VERSION == 1
    assert data["url"] == "https://example.com/about"
    assert data["url_hash"] == url_hash(data["url"])
    assert data["crawl_job_id"] == "crawl-job-667"
    assert data["job_id"] == "scout-job-667"
    assert isinstance(data["future_crawl_meta"], dict)


def test_publisher_and_consumer_agree_on_the_fixture():
    data = _load()
    published = _publish(data)
    consumed = consume_stage2_url_record(published)
    assert published == data
    assert consumed == data
    assert consume_stage2_url_record(consumed) == consumed
    for name in ("envelope_version", "url", "url_hash", "crawl_job_id", "job_id"):
        assert type(published[name]) is type(data[name])
        assert type(consumed[name]) is type(data[name])
    assert published["envelope_version"] == 1
    assert isinstance(published["priority"], int) and not isinstance(published["priority"], bool)
    assert isinstance(published["is_heavy"], bool)


def test_unknown_optional_field_is_kept_at_both_boundaries():
    data = _load()
    published = _publish(data)
    consumed = consume_stage2_url_record(data)
    assert published["future_crawl_meta"] == {"extractor": "url_extractor", "section": "about"}
    assert consumed["future_crawl_meta"] == data["future_crawl_meta"]
    assert "future_crawl_meta" not in REQUIRED_FIELDS
    assert "future_crawl_meta" not in OPTIONAL_FIELDS


def test_known_optional_may_be_omitted_and_parent_url_may_be_null():
    data = _load()
    minimal = {name: data[name] for name in REQUIRED_FIELDS}
    consumed = consume_stage2_url_record(minimal)
    assert list(consumed) == list(REQUIRED_FIELDS)
    published = publish_stage2_url_record(
        url=data["url"],
        crawl_job_id=data["crawl_job_id"],
        job_id=data["job_id"],
        parent_url=None,
    )
    assert published["parent_url"] is None
    assert consume_stage2_url_record(published)["parent_url"] is None


def test_required_field_types_fail_the_same_way_at_both_boundaries():
    data = _load()
    bad = dict(data)
    bad["priority"] = "high"
    with pytest.raises(Stage2UrlEnvelopeError) as published:
        _publish(bad, priority="high")
    with pytest.raises(Stage2UrlEnvelopeError) as consumed:
        consume_stage2_url_record(bad)
    assert str(published.value) == str(consumed.value)
    assert str(consumed.value) == ("stage1->stage2 URL envelope invalid: field priority has type str, expected int")

    blank = dict(data)
    blank["crawl_job_id"] = "  "
    with pytest.raises(Stage2UrlEnvelopeError) as missing:
        consume_stage2_url_record(blank)
    assert str(missing.value) == ("stage1->stage2 URL envelope invalid: missing required field crawl_job_id")


@pytest.mark.parametrize("version", [2, 0, "1", True, None])
def test_mismatched_version_fails_clearly_and_deterministically(version: object):
    data = _load()
    data["envelope_version"] = version
    messages = []
    for _ in range(2):
        with pytest.raises(Stage2UrlEnvelopeError) as caught:
            consume_stage2_url_record(data)
        messages.append(str(caught.value))
    assert messages[0] == messages[1]
    assert messages[0] == (f"stage1->stage2 URL envelope version mismatch: got {version!r}, expected 1")
    # Version is reported alone, even when other required fields are absent.
    with pytest.raises(Stage2UrlEnvelopeError) as only_version:
        consume_stage2_url_record({"envelope_version": version})
    assert str(only_version.value) == messages[0]


def test_pre_envelope_rows_stay_readable():
    legacy = {"url": "https://example.com/about", "status": "pending", "note": {"keep": True}}
    assert consume_stage2_url_record(legacy) == legacy
    assert consume_stage2_url_record(legacy) is not legacy


def test_publisher_rejects_extra_that_overrides_the_contract():
    with pytest.raises(Stage2UrlEnvelopeError) as caught:
        publish_stage2_url_record(
            url="https://example.com/about",
            crawl_job_id="crawl-job-667",
            job_id="scout-job-667",
            extra={"envelope_version": 2, "url": "https://example.com/other"},
        )
    assert str(caught.value) == (
        "stage1->stage2 URL envelope extra field(s) conflict with the contract: envelope_version, url"
    )


def test_scout_queue_for_stage2_publishes_the_envelope(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("src.otel_tracing.get_crawl_job_id", lambda: "crawl-job-667")

    class _Stub:
        job_id = "scout-job-667"

        @staticmethod
        def _hash_url(url: str) -> str:
            return url_hash(url)

    row = ScoutSpider._queue_for_stage2(_Stub(), "https://example.com/about", "https://example.com/dept/", "html")
    consumed = consume_stage2_url_record(row)
    assert consumed["envelope_version"] == 1
    assert consumed["url"] == "https://example.com/about"
    assert consumed["url_hash"] == url_hash(consumed["url"])
    assert consumed["crawl_job_id"] == "crawl-job-667"
    assert consumed["job_id"] == "scout-job-667"
    assert consumed["parent_url"] == "https://example.com/dept/"
    assert consumed["content_hint"] == "html"
    assert consumed["priority"] == 2
    assert consumed["target_stage"] == "stage2"
    assert consumed["status"] == "pending"

    pdf = ScoutSpider._queue_for_stage2(_Stub(), "https://example.com/handbook.pdf", "https://example.com/", "pdf")
    assert pdf["priority"] == 1
    assert pdf["content_hint"] == "pdf"
    assert consume_stage2_url_record(pdf)["envelope_version"] == 1


def test_stage2_worker_rejects_a_mismatched_version_before_fetch(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SSRF_ALLOWED_HOSTS", raising=False)
    from src.stage2.stage2_worker import Stage2Worker

    worker = Stage2Worker(max_concurrent=1)
    worker._session = _BoomSession()
    record = _load()
    record["url"] = _SSRF_URL
    record["envelope_version"] = 2
    with pytest.raises(Stage2UrlEnvelopeError) as caught:
        asyncio.run(worker._analyze_url(record))
    assert str(caught.value) == "stage1->stage2 URL envelope version mismatch: got 2, expected 1"


def test_stage2_worker_accepts_version_1_and_pre_envelope_rows(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SSRF_ALLOWED_HOSTS", raising=False)
    from src.stage2.stage2_worker import Stage2Worker

    worker = Stage2Worker(max_concurrent=1)
    worker._session = _BoomSession()
    versioned = _load()
    versioned["url"] = _SSRF_URL
    versioned["url_hash"] = "ab" * 8
    row = asyncio.run(worker._analyze_url(versioned))
    assert row["error_message"] == "ssrf_blocked:ip_link_local"

    legacy = asyncio.run(worker._analyze_url({"url": _SSRF_URL, "url_hash": "h"}))
    assert legacy["error_message"] == "ssrf_blocked:ip_link_local"
