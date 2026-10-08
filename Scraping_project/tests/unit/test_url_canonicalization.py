"""#728: one canonical URL across Redis, the queue pipeline, SeedManager and Stage 2."""

import pytest

from src.lakehouse.seed_manager import default_url_hasher
from src.pipelines import _canonical_queue_row
from src.stage1.processors.url_processor import URLProcessor
from src.stage2.stage2_worker import ensure_url_hash
from src.utils.url_canon import canonical_or_raw, canonicalize_url, url_hash
from src.utils.validation import normalize_url

VARIANTS = [
    "https://UConn.EDU/Admissions/",
    "https://uconn.edu/admissions",
    "https://uconn.edu:443/admissions/",
    "https://uconn.edu/admissions?utm_source=email&utm_medium=x",
    "https://uconn.edu/admissions/?fbclid=abc#apply",
    "HTTPS://uconn.edu/admissions?utm_custom_thing=1",
]
CANON = "https://uconn.edu/admissions"


@pytest.mark.parametrize("variant", VARIANTS)
def test_variants_share_canonical_url_and_hash(variant):
    assert canonicalize_url(variant) == CANON
    assert url_hash(variant) == url_hash(CANON)


def test_trailing_slash_host_case_and_utm():
    assert canonicalize_url("http://Example.edu:80/a/b/") == "http://example.edu/a/b"
    assert canonicalize_url("https://example.edu/") == "https://example.edu/"
    assert canonicalize_url("https://example.edu") == "https://example.edu/"
    assert canonicalize_url("https://x.edu/p?b=2&a=1&utm_campaign=z") == "https://x.edu/p?a=1&b=2"


def test_non_tracking_params_kept():
    # validation.normalize_url used to drop anything *starting with* ref/source/campaign
    assert canonicalize_url("https://x.edu/s?reference=7&sources=all") == "https://x.edu/s?reference=7&sources=all"


@pytest.mark.parametrize("bad", ["mailto:a@b.edu", "javascript:void(0)", "/relative/path", "", "ftp://x.edu/f"])
def test_non_http_rejected(bad):
    assert canonicalize_url(bad) is None
    assert canonical_or_raw(bad) == bad


@pytest.mark.parametrize("variant", VARIANTS)
def test_idempotent(variant):
    once = canonicalize_url(variant)
    assert canonicalize_url(once) == once


def test_all_helpers_agree():
    proc = URLProcessor(base_url="", allowed_domains=[], use_historical_data=False)
    for v in VARIANTS:
        assert proc.normalize_url(v) == CANON
        assert normalize_url(v) == CANON
        assert proc.hash_url(v) == default_url_hasher(v) == url_hash(CANON)
    rows = ensure_url_hash([{"url": VARIANTS[3]}])
    assert rows[0]["url_hash"] == url_hash(CANON)


def test_queue_row_canonical_before_lake_write():
    item = {"url": VARIANTS[4], "target_stage": "stage2", "status": "pending"}
    row = _canonical_queue_row(item)
    assert row["url"] == CANON and row["url_hash"] == url_hash(CANON)
    assert item["url"] == VARIANTS[4]  # original item untouched


def test_redis_seen_set_dedupes_variants():
    fakeredis = pytest.importorskip("fakeredis")
    from src.utils.redis import RedisHelper

    mgr = RedisHelper()
    mgr._client = fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
    assert mgr.claim_url(VARIANTS[0], "t") is True
    assert mgr.claim_url(VARIANTS[3], "t") is False  # same page, tracking variant
    assert mgr.check_url_seen(VARIANTS[5], "t") is True
    assert mgr.claim_urls([VARIANTS[1], "https://uconn.edu/other/", "https://UCONN.edu/other"], "t") == [
        "https://uconn.edu/other/"
    ]
    assert mgr.client.smembers("t:urls") == {CANON, "https://uconn.edu/other"}
