"""Tests for storage utilities using sample data."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from common.storage import JSONLStorage, URLCache, ConfigurableStorage


@pytest.mark.parametrize("records", [1, 5, 20])
def test_jsonl_storage_append_and_read(tmp_path, records):
    storage = JSONLStorage(tmp_path / "data.jsonl")
    sample = [{"idx": i, "value": f"record-{i}"} for i in range(records)]
    storage.append_batch(sample)

    read_back = list(storage.read_all())
    assert len(read_back) == records
    for original, loaded in zip(sample, read_back):
        assert original == loaded


@pytest.mark.parametrize("batch_sizes", [[1, 1, 1], [2, 3], [5]])
def test_jsonl_storage_append_batch(tmp_path, batch_sizes):
    storage = JSONLStorage(tmp_path / "data.jsonl")
    count = 0
    for size in batch_sizes:
        payload = [{"idx": count + i} for i in range(size)]
        storage.append_batch(payload)
        count += size

    assert storage.count_lines() == count


def test_jsonl_storage_exists(tmp_path):
    storage = JSONLStorage(tmp_path / "data.jsonl")
    assert storage.exists() is False
    storage.append({"idx": 1})
    assert storage.exists() is True


def test_url_cache_add_and_get(tmp_path):
    cache = URLCache(tmp_path / "cache.sqlite")
    cache.add_discovery("https://uconn.edu/a", "hash_a", "2024-01-01T00:00:00")
    record = cache.get_url("hash_a")
    assert record["url"] == "https://uconn.edu/a"


def test_url_cache_validation_and_enrichment(tmp_path):
    cache = URLCache(tmp_path / "cache.sqlite")
    cache.add_discovery("https://uconn.edu/a", "hash_a", "2024-01-01T00:00:00")
    cache.update_validation("hash_a", "2024-01-01T01:00:00", 200, True, "text/html")
    cache.update_enrichment("hash_a", "2024-01-01T02:00:00", title="Title", word_count=120)

    record = cache.get_url("hash_a")
    assert record["status_code"] == 200
    assert record["title"] == "Title"


@pytest.mark.parametrize("is_valid", [True, False])
def test_url_cache_filters(tmp_path, is_valid):
    cache = URLCache(tmp_path / "cache.sqlite")
    cache.add_discovery("https://uconn.edu/a", "hash_a", "2024-01-01T00:00:00")
    cache.update_validation("hash_a", "2024-01-01T01:00:00", 200 if is_valid else 404, is_valid, "text/html")

    results = cache.get_urls_by_status(is_valid=is_valid)
    assert len(results) == 1
    assert results[0]["is_valid"] == is_valid


def test_url_cache_stats(tmp_path):
    cache = URLCache(tmp_path / "cache.sqlite")
    cache.add_discovery("https://uconn.edu/a", "hash_a", "2024-01-01T00:00:00")
    cache.add_discovery("https://uconn.edu/b", "hash_b", "2024-01-01T00:05:00")
    cache.update_validation("hash_a", "2024-01-01T01:00:00", 200, True, "text/html")
    cache.update_enrichment("hash_a", "2024-01-01T02:00:00", title="Title", word_count=100)

    stats = cache.get_stats()
    assert stats["total_urls"] == 2
    assert stats["validated_urls"] >= 1
    assert stats["enriched_urls"] >= 1


@pytest.mark.parametrize("backend,expected_class", [("jsonl", "JSONLStorage"), ("sqlite", "URLCache")])
def test_configurable_storage_selects_backend(tmp_path, backend, expected_class):
    storage = ConfigurableStorage(backend, tmp_path / "storage.dat")
    assert storage.get_storage().__class__.__name__ == expected_class


def test_url_cache_database_integrity(tmp_path):
    db_path = tmp_path / "cache.sqlite"
    URLCache(db_path)
    with sqlite3.connect(db_path) as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert any(row[0] == "urls" for row in tables)


def test_configurable_storage_invalid_backend(tmp_path):
    with pytest.raises(ValueError):
        ConfigurableStorage("unknown", tmp_path / "storage.dat")
