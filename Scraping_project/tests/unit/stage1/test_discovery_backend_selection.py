"""#593: one sitemap discovery backend per Stage 1 start.

rustmapper's ``sitemap.jsonl`` handoff replaces the Python sitemap walk when
``stage1.discovery.rustmapper_enabled`` is on. The Python walk runs only as the
fallback after a FAILED handoff. Domain-table writes follow config, not a
hard-coded UConn flag. No Redis or network is needed: the spider is built
without ``__init__`` and the Python walk is replaced by a recorder.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

from src.core.config import get_config
from src.lakehouse.lakehouse_manager import InMemoryBackend
from src.lakehouse.seed_manager import SeedManager
from src.stage1 import scout_spider
from src.stage1.discovery_dedup import DiscoveryDedup
from src.stage1.discovery_handoff import DiscoveryHandoff, ingest_handoff_via_seed_manager
from src.stage1.scout_spider import (
    BACKEND_PYTHON_FALLBACK,
    BACKEND_PYTHON_SITEMAP,
    BACKEND_RUSTMAPPER,
    ScoutSpider,
    _stage1_flag,
    _stage1_path,
)
from src.utils.metric_labels import sample_labels

BASE = "https://example.edu/"
PY_URLS = ["https://example.edu/py-1", "https://example.edu/py-2"]
RS_URLS = ["https://example.edu/rs-1", "https://example.edu/rs-2"]


class FakeConfig:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeSeeds:
    def __init__(self):
        self.calls: list[dict] = []

    def add_urls_to_seeds(self, **kwargs):
        self.calls.append(kwargs)
        return {"seed_inserted": len(kwargs["urls"]), "domain_inserted": 0, "uconn_inserted": 0, "stage2_enqueued": 0}


def _spider(*, backend=BACKEND_PYTHON_SITEMAP, jsonl=None, handoff_path=None, fallback=True, mode="shared_dedup"):
    spider = ScoutSpider.__new__(ScoutSpider)
    spider.start_urls = [BASE]
    spider.job_id = "job-593"
    spider.ack_empty_discovery = False
    spider.scout_stats = {
        "discovery_dedup_skipped": 0,
        "discovery_empty_acks": 0,
        "discovery_failures": 0,
        "urls_added_to_seeds": 0,
    }
    spider.discovery_dedup = DiscoveryDedup(mode=mode, job_id="job-593")
    spider.seed_manager = FakeSeeds()
    spider.discovery_backend = backend
    spider.rustmapper_enabled = backend == BACKEND_RUSTMAPPER
    spider.rustmapper_fallback = fallback
    spider.rustmapper_jsonl_path = jsonl
    spider.rustmapper_handoff_path = handoff_path
    spider._last_discovery_handoff = None
    return spider


def _jsonl(tmp_path: Path, urls: list[str], name: str = "sitemap.jsonl") -> str:
    path = tmp_path / name
    path.write_text("".join(f"{url}\n" for url in urls), encoding="utf-8")
    return str(path)


def _backend_count(backend: str) -> float:
    value = REGISTRY.get_sample_value(
        "stage1_discovery_backend_total",
        sample_labels("stage1_discovery_backend", spider="scout", backend=backend),
    )
    return value or 0.0


@pytest.fixture
def python_walk(monkeypatch):
    """Replace the Python sitemap walk with a recorder that returns PY_URLS."""
    calls: list[str] = []

    def fake_walk(base_url, timeout=30, *, discovery_source="sitemap_parser", job_id=None):
        calls.append(base_url)
        return DiscoveryHandoff.success(PY_URLS, discovery_source=discovery_source, job_id=job_id)

    monkeypatch.setattr(scout_spider, "discover_sitemaps_handoff_sync", fake_walk)
    return calls


# ----------------------------------------------------------------- config keys


def test_repo_config_keeps_the_python_walk_as_the_default():
    cfg = get_config()
    assert _stage1_flag(cfg, "discovery.rustmapper_enabled", True) is False
    assert _stage1_flag(cfg, "discovery.rustmapper_fallback", False) is True
    assert _stage1_path(cfg, "discovery.jsonl_path") is None


def test_flags_and_paths_read_both_config_layouts():
    assert _stage1_flag(FakeConfig({"stages.stage1.discovery.rustmapper_enabled": "true"}), "discovery.rustmapper_enabled", False) is True
    assert _stage1_flag(FakeConfig({"stage1.discovery.rustmapper_fallback": "no"}), "discovery.rustmapper_fallback", True) is False
    assert _stage1_path(FakeConfig({"stage1.discovery.jsonl_path": "  "}), "discovery.jsonl_path") is None
    assert (
        _stage1_path(FakeConfig({"stages.stage1.discovery.jsonl_path": " /data/sitemap.jsonl "}), "discovery.jsonl_path")
        == "/data/sitemap.jsonl"
    )


# ------------------------------------------------------------ one backend per run


def test_python_backend_never_reads_the_rustmapper_handoff(tmp_path, python_walk, monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("rustmapper handoff must not be read when the Python backend is selected")

    monkeypatch.setattr(scout_spider, "consume_rustmapper_handoff", must_not_run)
    spider = _spider(backend=BACKEND_PYTHON_SITEMAP, jsonl=_jsonl(tmp_path, RS_URLS))
    before = _backend_count(BACKEND_PYTHON_SITEMAP)

    spider._discover_and_add_sitemap_urls()

    assert python_walk == [BASE]
    assert spider.seed_manager.calls[0]["source_spider"] == "sitemap_parser"
    assert _backend_count(BACKEND_PYTHON_SITEMAP) == before + 1


def test_rustmapper_success_replaces_the_python_walk(tmp_path, python_walk, caplog):
    spider = _spider(backend=BACKEND_RUSTMAPPER, jsonl=_jsonl(tmp_path, RS_URLS))
    before = _backend_count(BACKEND_RUSTMAPPER)

    with caplog.at_level(logging.WARNING):
        spider._discover_and_add_sitemap_urls()

    assert python_walk == []
    assert len(spider.seed_manager.calls) == 1
    call = spider.seed_manager.calls[0]
    assert sorted(call["urls"]) == sorted(RS_URLS)
    assert call["source_spider"] == "rustmapper"
    assert "Python sitemap parser is deprecated for example.edu" in caplog.text
    assert _backend_count(BACKEND_RUSTMAPPER) == before + 1


def test_empty_rustmapper_handoff_never_falls_back(tmp_path, python_walk):
    spider = _spider(backend=BACKEND_RUSTMAPPER, jsonl=_jsonl(tmp_path, []), fallback=True)

    spider._discover_and_add_sitemap_urls()

    assert spider._last_discovery_handoff.is_empty
    assert python_walk == []
    assert spider.seed_manager.calls == []
    assert spider.scout_stats["discovery_failures"] == 0


def _missing_jsonl(tmp_path: Path) -> dict:
    return {"jsonl": str(tmp_path / "missing.jsonl")}


def _unset_jsonl(tmp_path: Path) -> dict:
    return {"jsonl": None}


def _corrupt_sidecar(tmp_path: Path) -> dict:
    sidecar = tmp_path / "handoff.json"
    sidecar.write_text("{not json", encoding="utf-8")
    return {"jsonl": _jsonl(tmp_path, RS_URLS), "handoff_path": str(sidecar)}


@pytest.mark.parametrize(
    "make_inputs",
    [_missing_jsonl, _unset_jsonl, _corrupt_sidecar],
    ids=["missing-jsonl", "jsonl-path-unset", "unreadable-sidecar"],
)
def test_failed_rustmapper_handoff_falls_back_to_python(tmp_path, python_walk, make_inputs):
    spider = _spider(backend=BACKEND_RUSTMAPPER, fallback=True, **make_inputs(tmp_path))
    before = _backend_count(BACKEND_PYTHON_FALLBACK)

    spider._discover_and_add_sitemap_urls()

    assert python_walk == [BASE]
    assert spider.scout_stats["discovery_failures"] == 1
    assert [c["source_spider"] for c in spider.seed_manager.calls] == ["sitemap_parser"]
    assert sorted(spider.seed_manager.calls[0]["urls"]) == sorted(PY_URLS)
    assert _backend_count(BACKEND_PYTHON_FALLBACK) == before + 1


def test_failed_rustmapper_handoff_without_fallback_aborts(tmp_path, python_walk):
    spider = _spider(backend=BACKEND_RUSTMAPPER, jsonl=str(tmp_path / "missing.jsonl"), fallback=False)

    spider._discover_and_add_sitemap_urls()

    assert spider._last_discovery_handoff.is_failed
    assert python_walk == []
    assert spider.seed_manager.calls == []
    assert spider.scout_stats["discovery_failures"] == 1


def test_mutex_owned_by_another_engine_skips_both_backends(tmp_path, python_walk):
    spider = _spider(backend=BACKEND_RUSTMAPPER, jsonl=_jsonl(tmp_path, RS_URLS), mode="mutex", fallback=True)
    assert spider.discovery_dedup.acquire_mutex("example.edu", "other-engine") is True

    spider._discover_and_add_sitemap_urls()

    assert python_walk == []
    assert spider.seed_manager.calls == []


def test_provenance_distinguishes_the_two_backends(tmp_path, python_walk):
    rustmapper = _spider(backend=BACKEND_RUSTMAPPER, jsonl=_jsonl(tmp_path, RS_URLS))
    python = _spider(backend=BACKEND_PYTHON_SITEMAP)

    rustmapper._discover_and_add_sitemap_urls()
    python._discover_and_add_sitemap_urls()

    assert rustmapper.seed_manager.calls[0]["source_spider"] == "rustmapper"
    assert python.seed_manager.calls[0]["source_spider"] == "sitemap_parser"


# ---------------------------------------------------------- UConn hard-code gone


def test_sitemap_ingest_passes_no_uconn_flag_to_seed_manager():
    spider = _spider(backend=BACKEND_PYTHON_SITEMAP)

    spider._ingest_discovery_handoff(
        DiscoveryHandoff.success(PY_URLS, discovery_source="sitemap_parser"),
        source_url=f"{BASE}sitemap.xml",
    )

    call = spider.seed_manager.calls[0]
    assert "write_uconn_urls" not in call
    assert call.get("write_domain_urls") is None
    assert call["enqueue_stage2"] is False


def test_handoff_ingest_follows_config_and_skips_the_uconn_table_by_default():
    backend = InMemoryBackend()
    sm = SeedManager(backend, config=FakeConfig({"stage1.allowed_domains": ["example.org"]}))
    assert sm.write_domain_urls is False

    result = ingest_handoff_via_seed_manager(
        sm,
        DiscoveryHandoff.success(["https://example.org/a"], discovery_source="rustmapper"),
        source_url="https://example.org/sitemap.xml",
        enqueue_stage2=False,
    )

    assert result["seed_inserted"] == 1
    assert "uconn_urls" not in backend.tables


def test_handoff_ingest_writes_the_domain_table_only_when_the_profile_opts_in():
    backend = InMemoryBackend()
    sm = SeedManager(
        backend,
        config=FakeConfig(
            {
                "stage1.write_domain_urls": "true",
                "stage1.allowed_domains": ["example.org"],
                "stage1.domain_urls_table": "example_urls",
            }
        ),
    )

    ingest_handoff_via_seed_manager(
        sm,
        DiscoveryHandoff.success(["https://example.org/a"], discovery_source="sitemap_parser"),
        source_url="https://example.org/sitemap.xml",
        enqueue_stage2=False,
    )

    assert [r["url"] for r in backend.tables["example_urls"]] == ["https://example.org/a"]
    assert "uconn_urls" not in backend.tables


# ------------------------------------------------------------------- metric


def test_backend_metric_labels_follow_the_contract():
    pytest.importorskip("prometheus_client")
    from src import scrapy_prometheus as sp
    from src.utils.metric_labels import SCRAPY_FAMILIES

    assert tuple(sp.STAGE1_DISCOVERY_BACKEND._labelnames) == SCRAPY_FAMILIES["stage1_discovery_backend"]
