"""#496: the HTTP cache stays under HTTPCACHE_MAX_BYTES by pruning the oldest entries."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import scrapy
from prometheus_client import REGISTRY
from scrapy.exceptions import NotConfigured
from scrapy.extensions.httpcache import FilesystemCacheStorage
from scrapy.http import HtmlResponse, Request
from scrapy.settings import Settings
from scrapy.utils.test import get_crawler

from src.stage1.extensions import httpcache_quota as hq


def _entry(root: Path, name: str, size: int, mtime: float) -> Path:
    d = root / "spider" / name[:2] / name
    d.mkdir(parents=True)
    (d / "response_body").write_bytes(b"x" * size)
    (d / hq.ENTRY_MARKER).write_bytes(b"m")
    os.utime(d / hq.ENTRY_MARKER, (mtime, mtime))
    return d


def _sample(name):
    return REGISTRY.get_sample_value(name) or 0.0


def test_under_quota_prunes_nothing(tmp_path):
    _entry(tmp_path, "aa1", 100, 1)
    r = hq.prune_cache(tmp_path, max_bytes=10_000)
    assert r.pruned_entries == 0 and r.total_bytes == 101


def test_over_quota_prunes_oldest_first_down_to_target(tmp_path):
    old = _entry(tmp_path, "aa1", 1000, 100)
    mid = _entry(tmp_path, "bb2", 1000, 200)
    new = _entry(tmp_path, "cc3", 1000, 300)
    before = _sample("scrapy_httpcache_pruned_entries_total")
    r = hq.prune_cache(tmp_path, max_bytes=2500, target_ratio=0.8)  # 3003 > 2500, target 2000
    assert not old.exists() and not mid.exists() and new.exists()
    assert r.pruned_entries == 2 and r.total_bytes == 1001
    assert _sample("scrapy_httpcache_pruned_entries_total") == before + 2
    assert _sample("scrapy_httpcache_bytes") == 1001


def test_zero_quota_only_measures(tmp_path):
    e = _entry(tmp_path, "aa1", 5000, 1)
    r = hq.prune_cache(tmp_path, max_bytes=0)
    assert e.exists() and r.total_bytes == 5001 and r.pruned_entries == 0


def test_dbm_style_file_is_counted_but_never_deleted(tmp_path):
    (tmp_path / "spider.db").write_bytes(b"d" * 4000)
    _entry(tmp_path, "aa1", 100, 1)
    r = hq.prune_cache(tmp_path, max_bytes=1000)
    assert (tmp_path / "spider.db").exists()
    assert r.unprunable_bytes == 4000 and r.pruned_entries == 1


def test_undeletable_entry_is_counted_not_raised(tmp_path, monkeypatch):
    _entry(tmp_path, "aa1", 1000, 1)
    _entry(tmp_path, "bb2", 1000, 2)
    real = shutil.rmtree

    def flaky(path, *a, **k):
        if Path(path).name == "aa1":
            raise PermissionError("busy")
        return real(path, *a, **k)

    monkeypatch.setattr(hq.shutil, "rmtree", flaky)
    r = hq.prune_cache(tmp_path, max_bytes=1500, target_ratio=0.5)
    assert r.errors == 1 and r.pruned_entries == 1


def test_missing_cache_dir_is_fine(tmp_path):
    assert hq.prune_cache(tmp_path / "nope", max_bytes=10).total_bytes == 0


def test_prunes_real_scrapy_filesystem_cache(tmp_path):
    """End to end with Scrapy's own FilesystemCacheStorage layout."""
    storage = FilesystemCacheStorage(Settings({"HTTPCACHE_DIR": str(tmp_path), "HTTPCACHE_EXPIRATION_SECS": 0}))
    spider = scrapy.Spider(name="quota")
    spider.crawler = get_crawler(settings_dict={"HTTPCACHE_DIR": str(tmp_path)})
    storage.open_spider(spider)
    reqs = []
    for i in range(5):
        req = Request(f"https://www.uconn.edu/p{i}")
        storage.store_response(spider, req, HtmlResponse(req.url, body=b"y" * 2000, request=req))
        reqs.append(req)
    entries, total, _ = hq.scan_cache(tmp_path)
    assert len(entries) == 5
    # p0 oldest ... p4 newest
    for age, req in enumerate(reqs):
        meta = Path(storage._get_request_path(spider, req)) / hq.ENTRY_MARKER
        os.utime(meta, (1000 + age, 1000 + age))
    r = hq.prune_cache(tmp_path, max_bytes=total - 1, target_ratio=0.7)
    assert r.pruned_entries >= 2
    assert storage.retrieve_response(spider, reqs[0]) is None  # oldest gone
    assert storage.retrieve_response(spider, reqs[-1]) is not None  # newest still served
    storage.close_spider(spider)


def test_extension_not_configured_when_cache_disabled():
    with pytest.raises(NotConfigured):
        hq.HttpCacheQuota.from_crawler(get_crawler(settings_dict={"HTTPCACHE_ENABLED": False}))


def test_extension_prunes_on_open_and_close(tmp_path):
    crawler = get_crawler(
        settings_dict={
            "HTTPCACHE_ENABLED": True,
            "HTTPCACHE_DIR": str(tmp_path),
            "HTTPCACHE_MAX_BYTES": 1500,
            "HTTPCACHE_PRUNE_INTERVAL_SECS": 0,
        }
    )
    ext = hq.HttpCacheQuota.from_crawler(crawler)
    _entry(tmp_path, "aa1", 1000, 1)
    _entry(tmp_path, "bb2", 1000, 2)
    ext.spider_opened()
    assert ext.last.pruned_entries == 1
    assert _sample("scrapy_httpcache_quota_bytes") == 1500
    _entry(tmp_path, "cc3", 1000, 3)
    ext.spider_closed()
    assert ext.last.pruned_entries == 1 and ext.last.total_bytes <= 1500


def test_settings_register_extension_with_prunable_storage():
    import src.settings as s

    assert "src.stage1.extensions.httpcache_quota.HttpCacheQuota" in s.EXTENSIONS
    assert s.HTTPCACHE_STORAGE.endswith("FilesystemCacheStorage")
    assert s.HTTPCACHE_MAX_BYTES > 0
