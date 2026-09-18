"""Unit tests for restored SeedManager (#607)."""

from __future__ import annotations

from src.lakehouse import SeedManager
from src.lakehouse.lakehouse_manager import InMemoryBackend
from src.lakehouse.seed_manager import create_seed_manager_from_delta, default_url_hasher


def test_import_seed_manager_is_real_class():
    assert isinstance(SeedManager, type)
    assert callable(default_url_hasher)
    assert callable(create_seed_manager_from_delta)


def test_add_urls_to_seeds_writes_seed_rows():
    backend = InMemoryBackend()
    sm = SeedManager(backend)
    urls = [
        "https://uconn.edu/about/",
        "https://example.com/offsite",
        "https://uconn.edu/about/",  # dup within batch
    ]
    result = sm.add_urls_to_seeds(
        urls=urls,
        source_url="https://uconn.edu/",
        source_spider="scout",
        write_uconn_urls=True,
        enqueue_stage2=False,
    )
    assert result["seed_inserted"] == 2
    assert result["uconn_inserted"] == 1
    assert result["stage2_enqueued"] == 0

    seeds = backend.read("seed_urls")
    assert len(seeds) == 2
    hashes = {r["url_hash"] for r in seeds}
    assert default_url_hasher("https://uconn.edu/about/") in hashes


def test_enqueue_stage2():
    backend = InMemoryBackend()
    sm = SeedManager(backend)
    result = sm.add_urls_to_seeds(
        urls=["https://uconn.edu/page"],
        source_url="seed",
        source_spider="manual",
        write_uconn_urls=False,
        enqueue_stage2=True,
    )
    assert result["seed_inserted"] == 1
    assert result["stage2_enqueued"] == 1
    q = backend.read("stage2_queue")
    assert len(q) == 1
    assert q[0]["status"] == "pending"


def test_create_seed_manager_from_delta_compat():
    backend = InMemoryBackend()
    sm = create_seed_manager_from_delta(backend)
    assert isinstance(sm, SeedManager)


def test_idempotent_merge_keeps_single_row():
    backend = InMemoryBackend()
    sm = SeedManager(backend)
    sm.add_urls_to_seeds(
        urls=["https://uconn.edu/a"],
        source_url="s",
        source_spider="scout",
        write_uconn_urls=False,
        enqueue_stage2=False,
    )
    sm.add_urls_to_seeds(
        urls=["https://uconn.edu/a"],
        source_url="s2",
        source_spider="scout",
        write_uconn_urls=False,
        enqueue_stage2=False,
    )
    assert len(backend.read("seed_urls")) == 1


def test_seed_manager_unwraps_delta_helper_manager():
    class FakeDelta:
        def __init__(self):
            self.manager = InMemoryBackend()

    sm = SeedManager(FakeDelta())
    result = sm.add_urls_to_seeds(
        urls=["https://uconn.edu/x"],
        source_url="t",
        source_spider="t",
        write_uconn_urls=False,
        enqueue_stage2=False,
    )
    assert result["seed_inserted"] == 1
