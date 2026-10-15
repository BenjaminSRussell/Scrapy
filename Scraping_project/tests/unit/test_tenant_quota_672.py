"""Per-profile concurrent-job and URL/day quotas (#672).

Null caps do not reject. A rejection names the profile and the cap, increments
quota_exceeded_total, and does not reserve a slot or spend the day's URLs.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import yaml
from prometheus_client import REGISTRY

from src.core.crawl_profile import (
    REASON_CONCURRENT_JOBS,
    REASON_URLS_PER_DAY,
    CrawlProfile,
    QuotaExceededError,
    admit_job,
    quota_usage,
    reset_quota_for_tests,
    resolve_crawl_profile,
)
from src.orchestrator import pipeline_orchestrator as po
from src.utils.validation import (
    ConfigValidationError,
    default_config_path,
    validate_config_document,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_ledger():
    reset_quota_for_tests()
    yield
    reset_quota_for_tests()


def _metric(profile: str, reason: str) -> float:
    return REGISTRY.get_sample_value(
        "quota_exceeded_total", {"profile": profile, "reason": reason}
    ) or 0.0


def _shipped() -> dict:
    loaded = yaml.safe_load(default_config_path().read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_shipped_profile_caps_are_null_and_valid():
    data = _shipped()
    assert data["crawl_profiles"]["uconn"] == {
        "max_concurrent_jobs": None,
        "max_urls_per_day": None,
    }
    assert data["stage1"]["write_domain_urls"] is True
    validate_config_document(data)


def test_null_caps_never_reject_or_record():
    profile = CrawlProfile(profile_id="uconn")
    jobs = _metric("uconn", REASON_CONCURRENT_JOBS)
    urls = _metric("uconn", REASON_URLS_PER_DAY)
    for _ in range(4):
        admit_job(profile, None).release()
    admit_job(profile, 10**9).release()
    assert quota_usage("uconn") == (0, 0)
    assert _metric("uconn", REASON_CONCURRENT_JOBS) == jobs
    assert _metric("uconn", REASON_URLS_PER_DAY) == urls


def test_concurrent_cap_rejects_without_taking_a_slot():
    profile = CrawlProfile(profile_id="uconn", max_concurrent_jobs=2)
    before = _metric("uconn", REASON_CONCURRENT_JOBS)
    first = admit_job(profile, 0)
    second = admit_job(profile, 0)
    with pytest.raises(QuotaExceededError, match="max_concurrent_jobs is 2") as exc:
        admit_job(profile, 0)
    err = exc.value
    assert err.limit == REASON_CONCURRENT_JOBS
    assert err.profile_id == "uconn"
    assert err.cap == 2
    assert err.observed == 2
    assert "not an authorization" in str(err)
    assert quota_usage("uconn")[0] == 2
    assert _metric("uconn", REASON_CONCURRENT_JOBS) == before + 1
    first.release()
    assert quota_usage("uconn")[0] == 1
    third = admit_job(profile, 0)
    assert quota_usage("uconn")[0] == 2
    second.release()
    third.release()
    assert quota_usage("uconn")[0] == 0


def test_url_cap_rejects_without_spending_and_resets_next_utc_day():
    profile = CrawlProfile(profile_id="uconn", max_urls_per_day=100)
    day = datetime(2026, 5, 1, 12, tzinfo=UTC)
    before = _metric("uconn", REASON_URLS_PER_DAY)
    held = admit_job(profile, 60, now=day)
    with pytest.raises(QuotaExceededError, match=r"max_urls_per_day is 100 \(60 URLs already used today, request is 50\)") as exc:
        admit_job(profile, 50, now=day)
    assert exc.value.limit == REASON_URLS_PER_DAY
    assert exc.value.observed == 60
    assert exc.value.requested == 50
    assert "not an authorization" in str(exc.value)
    # Concurrent cap is null, so only the URL charge is recorded.
    assert quota_usage("uconn", now=day) == (0, 60)
    assert _metric("uconn", REASON_URLS_PER_DAY) == before + 1
    held.release()
    rest = admit_job(profile, 40, now=day)
    assert quota_usage("uconn", now=day) == (0, 100)
    rest.release()
    with pytest.raises(QuotaExceededError, match="request is 1"):
        admit_job(profile, 1, now=day)
    nxt = day + timedelta(days=1)
    rolled = admit_job(profile, 100, now=nxt)
    assert quota_usage("uconn", now=nxt) == (0, 100)
    assert quota_usage("uconn", now=day)[1] == 100
    rolled.release()


def test_unbounded_url_request_is_rejected_when_a_daily_cap_is_set():
    profile = CrawlProfile(profile_id="uconn", max_concurrent_jobs=5, max_urls_per_day=10)
    before_jobs = _metric("uconn", REASON_CONCURRENT_JOBS)
    with pytest.raises(QuotaExceededError, match="no URL limit") as exc:
        admit_job(profile, None)
    assert exc.value.limit == REASON_URLS_PER_DAY
    assert quota_usage("uconn") == (0, 0)
    assert _metric("uconn", REASON_CONCURRENT_JOBS) == before_jobs
    admit_job(profile, 10).release()


def test_one_profile_does_not_spend_another():
    tight = CrawlProfile(profile_id="uconn", max_concurrent_jobs=1, max_urls_per_day=5)
    other = CrawlProfile(profile_id="example", max_concurrent_jobs=1, max_urls_per_day=5)
    held = admit_job(tight, 5)
    with pytest.raises(QuotaExceededError, match="uconn"):
        admit_job(tight, 0)
    admitted = admit_job(other, 5)
    assert quota_usage("example") == (1, 5)
    assert quota_usage("uconn") == (1, 5)
    held.release()
    admitted.release()


def test_zero_concurrent_cap_rejects_every_job():
    profile = CrawlProfile(profile_id="uconn", max_concurrent_jobs=0, max_urls_per_day=None)
    with pytest.raises(QuotaExceededError, match="max_concurrent_jobs is 0"):
        admit_job(profile, 0)
    assert quota_usage("uconn") == (0, 0)


def test_resolve_uses_explicit_profile_single_config_or_site_id(monkeypatch):
    monkeypatch.delenv("SITE_ID", raising=False)
    monkeypatch.delenv("TENANT_ID", raising=False)
    monkeypatch.delenv("REDIS_SITE_ID", raising=False)
    table = {"uconn": {"max_concurrent_jobs": 2, "max_urls_per_day": None}}
    assert resolve_crawl_profile(None, profiles={}).profile_id == "default"
    assert resolve_crawl_profile(None, profiles={}).max_concurrent_jobs is None
    chosen = resolve_crawl_profile(None, profiles=table)
    assert chosen.profile_id == "uconn"
    assert chosen.max_concurrent_jobs == 2
    given = CrawlProfile(profile_id="example", max_concurrent_jobs=4)
    assert resolve_crawl_profile(given, profiles=table) is given
    assert resolve_crawl_profile("example", profiles=table).max_concurrent_jobs is None
    monkeypatch.setenv("SITE_ID", "example")
    assert resolve_crawl_profile(None, profiles=table).profile_id == "example"
    with pytest.raises(ValueError, match="profile id"):
        resolve_crawl_profile("not a profile", profiles=table)
    with pytest.raises(ValueError, match="invalid quotas"):
        resolve_crawl_profile("uconn", profiles={"uconn": {"max_concurrent_jobs": True}})


def test_config_schema_accepts_nulls_and_rejects_bad_caps():
    data = _shipped()
    missing = dict(data)
    del missing["crawl_profiles"]
    validate_config_document(missing)
    null_block = dict(data)
    null_block["crawl_profiles"] = None
    validate_config_document(null_block)
    null_profile = dict(data)
    null_profile["crawl_profiles"] = {"example.com": None, "uconn": {}}
    validate_config_document(null_profile)
    zero = dict(data)
    zero["crawl_profiles"] = {"uconn": {"max_concurrent_jobs": 0, "max_urls_per_day": 10}}
    validate_config_document(zero)

    bad = dict(data)
    bad["crawl_profiles"] = {"uconn": {"max_concurrent_jobs": True}}
    with pytest.raises(ConfigValidationError, match=r"crawl_profiles\.uconn\.max_concurrent_jobs"):
        validate_config_document(bad)

    bad = dict(data)
    bad["crawl_profiles"] = {"uconn": {"max_urls_per_day": "10"}}
    with pytest.raises(ConfigValidationError, match=r"max_urls_per_day"):
        validate_config_document(bad)

    bad = dict(data)
    bad["crawl_profiles"] = {"uconn": {"max_concurrent_jobs": -1}}
    with pytest.raises(ConfigValidationError, match=r"max_concurrent_jobs"):
        validate_config_document(bad)

    bad = dict(data)
    bad["crawl_profiles"] = {"uconn": {"max_jobs": 1}}
    with pytest.raises(ConfigValidationError, match=r"max_jobs"):
        validate_config_document(bad)

    bad = dict(data)
    bad["crawl_profiles"] = {"bad id": {"max_concurrent_jobs": 1}}
    with pytest.raises(ConfigValidationError, match="bad id"):
        validate_config_document(bad)

    bad = dict(data)
    bad["crawl_profiles"] = ["uconn"]
    with pytest.raises(ConfigValidationError, match="mapping or null"):
        validate_config_document(bad)


def test_orchestrator_rejects_before_any_stage(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))
    events: list[str] = []
    orch = po.PipelineOrchestrator()

    def stage1(url_limit=None, spider_name="scout"):
        events.append("stage1")
        return 0

    monkeypatch.setattr(orch, "run_stage1", stage1)
    monkeypatch.setattr(po, "Stage2Worker", lambda **kwargs: (_ for _ in ()).throw(AssertionError("stage2")))
    profile = CrawlProfile(profile_id="uconn", max_concurrent_jobs=1, max_urls_per_day=10)
    before = _metric("uconn", REASON_URLS_PER_DAY)
    pending = orch.stats.status
    with pytest.raises(QuotaExceededError, match="max_urls_per_day is 10") as exc:
        asyncio.run(orch.run_full_pipeline(stage1_url_limit=11, profile=profile))
    assert "not an authorization" in str(exc.value)
    assert events == []
    assert orch.stats.status == pending
    assert quota_usage("uconn") == (0, 0)
    assert _metric("uconn", REASON_URLS_PER_DAY) == before + 1

    class _Worker:
        def __init__(self, **kwargs):
            events.append("worker")

        async def run(self):
            events.append("run")
            return {"analyzed": 1, "quality_docs": 1, "massive_docs": 0}

    monkeypatch.setattr(po, "Stage2Worker", _Worker)
    monkeypatch.setattr(po, "Stage3Worker", _Worker)
    monkeypatch.setattr(po, "Stage4Worker", _Worker)
    stats = asyncio.run(orch.run_full_pipeline(stage1_url_limit=10, profile=profile))
    assert stats.status == "complete"
    assert events[0] == "stage1"
    assert quota_usage("uconn") == (0, 10)

    with pytest.raises(QuotaExceededError, match="max_concurrent_jobs is 0"):
        orch.run_stage_by_name(
            "stage2",
            profile=CrawlProfile(profile_id="uconn", max_concurrent_jobs=0),
        )
    assert events.count("worker") == 3


def test_direct_stage1_rejects_before_the_crawler_starts(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))
    orch = po.PipelineOrchestrator()

    def boom(*_args, **_kwargs):
        raise AssertionError("crawler started")

    monkeypatch.setattr(po, "CrawlerProcess", boom)
    profile = CrawlProfile(profile_id="uconn", max_concurrent_jobs=0)
    with pytest.raises(QuotaExceededError, match="crawl profile 'uconn'"):
        orch.run_stage1(url_limit=5, profile=profile)
    assert quota_usage("uconn") == (0, 0)
