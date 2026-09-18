"""Unit tests for dual-discovery shared dedup + empty-vs-failed handoff (#647)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

try:
    from fakeredis import FakeRedis
except ImportError:  # pragma: no cover
    FakeRedis = None


@pytest.fixture
def mock_redis():
    if FakeRedis is None:
        pytest.skip("fakeredis not installed")
    return FakeRedis(decode_responses=True)


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    return tmp_path


from src.stage1.discovery_dedup import (
    DiscoveryDedup,
    DiscoveryDedupMode,
    get_discovery_dedup_from_config,
    seen_claim_key,
    url_hash,
)
from src.stage1.discovery_handoff import (
    DiscoveryEmptyNeedsAck,
    DiscoveryHandoff,
    DiscoveryHandoffError,
    DiscoveryStatus,
    checksum_urls,
    consume_rustmapper_handoff,
    ingest_handoff_via_seed_manager,
    require_ingestible,
    verify_checksum,
)


class TestDiscoveryDedupShared:
    def test_second_engine_cannot_double_claim(self):
        dedup = DiscoveryDedup(mode=DiscoveryDedupMode.SHARED_DEDUP, job_id="job-1")
        url = "https://example.edu/page-a"

        first = dedup.claim_url(url, "sitemap_parser")
        second = dedup.claim_url(url, "rustmapper")

        assert first.claimed is True
        assert first.discovery_source == "sitemap_parser"
        assert second.claimed is False
        assert second.prior_source == "sitemap_parser"

    def test_dual_engines_at_most_one_claimed_per_url(self):
        dedup = DiscoveryDedup(mode=DiscoveryDedupMode.SHARED_DEDUP, job_id="job-dual")
        urls = [
            "https://example.edu/a",
            "https://example.edu/b",
            "https://example.edu/c",
        ]

        claimed_py, _ = dedup.claim_urls(urls, "sitemap_parser")
        claimed_rs, results_rs = dedup.claim_urls(urls, "rustmapper")

        assert set(claimed_py) == set(urls)
        assert claimed_rs == []
        assert all(not r.claimed for r in results_rs)
        # <=1 pending claim winner per URL
        assert len(claimed_py) + len(claimed_rs) == len(urls)

    def test_redis_set_nx_claim(self, mock_redis):
        dedup = DiscoveryDedup(
            redis_client=mock_redis,
            mode=DiscoveryDedupMode.SHARED_DEDUP,
            job_id="job-redis",
        )
        url = "https://uconn.edu/research"
        assert dedup.claim_url(url, "scout").claimed is True
        assert dedup.claim_url(url, "rustmapper").claimed is False

        key = seen_claim_key("uconn.edu", url_hash(url), "job-redis")
        assert mock_redis.get(key) in (b"scout", "scout")

    def test_mutex_mode_blocks_second_engine(self):
        dedup = DiscoveryDedup(mode=DiscoveryDedupMode.MUTEX, job_id="job-m")
        assert dedup.acquire_mutex("uconn.edu", "sitemap_parser") is True
        assert dedup.acquire_mutex("uconn.edu", "rustmapper") is False
        dedup.release_mutex("uconn.edu", "sitemap_parser")
        assert dedup.acquire_mutex("uconn.edu", "rustmapper") is True


class TestDiscoveryHandoffStatus:
    def test_success_checksum(self):
        urls = ["https://a.example/1", "https://a.example/2"]
        handoff = DiscoveryHandoff.success(urls, discovery_source="sitemap_parser")
        assert handoff.status == DiscoveryStatus.SUCCESS
        assert verify_checksum(handoff.discovered, handoff.checksum)

    def test_empty_distinct_from_failed(self):
        empty = DiscoveryHandoff.empty(discovery_source="sitemap_parser")
        failed = DiscoveryHandoff.failed("boom", discovery_source="rustmapper")
        assert empty.status == DiscoveryStatus.EMPTY
        assert failed.status == DiscoveryStatus.FAILED
        assert empty.error is None
        assert failed.error == "boom"
        assert empty.to_dict()["status"] == "empty"
        assert failed.to_dict()["status"] == "failed"

    def test_require_ingestible_failed_aborts(self):
        handoff = DiscoveryHandoff.failed("crash", discovery_source="rustmapper")
        with pytest.raises(DiscoveryHandoffError):
            require_ingestible(handoff)

    def test_require_ingestible_empty_needs_ack(self):
        handoff = DiscoveryHandoff.empty(discovery_source="sitemap_parser")
        with pytest.raises(DiscoveryEmptyNeedsAck):
            require_ingestible(handoff)
        assert require_ingestible(handoff, ack_empty=True) == []

    def test_checksum_mismatch_aborts_ingest(self):
        handoff = DiscoveryHandoff(
            status=DiscoveryStatus.SUCCESS,
            discovered=["https://x.example/1"],
            checksum="deadbeef",
            discovery_source="scout",
        )
        with pytest.raises(DiscoveryHandoffError, match="checksum mismatch"):
            require_ingestible(handoff)


class TestRustmapperHandoffConsumer:
    def test_forced_crash_is_failed_not_empty(self, temp_dir: Path):
        jsonl = temp_dir / "sitemap.jsonl"
        jsonl.write_text("", encoding="utf-8")

        handoff = consume_rustmapper_handoff(
            jsonl,
            forced_failed=True,
            failure_error="rustmapper exited 139 (SIGSEGV)",
        )
        assert handoff.status == DiscoveryStatus.FAILED
        assert handoff.status != DiscoveryStatus.EMPTY
        assert "139" in (handoff.error or "")

    def test_empty_jsonl_is_empty_success_path_not_failed(self, temp_dir: Path):
        jsonl = temp_dir / "sitemap.jsonl"
        jsonl.write_text("\n", encoding="utf-8")
        handoff = consume_rustmapper_handoff(jsonl)
        assert handoff.status == DiscoveryStatus.EMPTY
        assert handoff.discovered == []

    def test_jsonl_urls_success_with_checksum(self, temp_dir: Path):
        jsonl = temp_dir / "sitemap.jsonl"
        lines = [
            json.dumps({"url": "https://example.edu/a"}),
            "https://example.edu/b",
        ]
        jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")
        handoff = consume_rustmapper_handoff(jsonl, site="example.edu")
        assert handoff.status == DiscoveryStatus.SUCCESS
        assert set(handoff.discovered) == {
            "https://example.edu/a",
            "https://example.edu/b",
        }
        assert handoff.checksum == checksum_urls(handoff.discovered)

    def test_failed_handoff_sidecar_not_reinterpreted_as_empty(self, temp_dir: Path):
        jsonl = temp_dir / "sitemap.jsonl"
        jsonl.write_text("", encoding="utf-8")
        sidecar = temp_dir / "handoff.json"
        failed = DiscoveryHandoff.failed("oom killed", discovery_source="rustmapper")
        sidecar.write_text(json.dumps(failed.to_dict()), encoding="utf-8")

        handoff = consume_rustmapper_handoff(jsonl, sidecar)
        assert handoff.status == DiscoveryStatus.FAILED
        assert handoff.error == "oom killed"


class TestIngestViaSeedManager:
    def test_ingest_claims_then_seeds(self):
        dedup = DiscoveryDedup(mode=DiscoveryDedupMode.SHARED_DEDUP, job_id="ingest")
        seed_mgr = MagicMock()
        seed_mgr.add_urls_to_seeds.return_value = {
            "seed_inserted": 1,
            "uconn_inserted": 0,
            "stage2_enqueued": 1,
        }
        urls = ["https://example.edu/only-once"]
        handoff = DiscoveryHandoff.success(urls, discovery_source="sitemap_parser")

        result = ingest_handoff_via_seed_manager(
            seed_mgr,
            handoff,
            source_url="https://example.edu/sitemap.xml",
            dedup=dedup,
            enqueue_stage2=True,
        )
        assert result["claimed"] == 1
        assert result["status"] == "success"
        seed_mgr.add_urls_to_seeds.assert_called_once()

        # Second engine (rustmapper) with same URLs -> no Stage2 double-enqueue
        handoff2 = DiscoveryHandoff.success(urls, discovery_source="rustmapper")
        result2 = ingest_handoff_via_seed_manager(
            seed_mgr,
            handoff2,
            source_url="https://example.edu/sitemap.xml",
            dedup=dedup,
            enqueue_stage2=True,
        )
        assert result2["claimed"] == 0
        assert result2["skipped_dedup"] == 1
        assert seed_mgr.add_urls_to_seeds.call_count == 1

    def test_failed_handoff_does_not_call_seed_manager(self):
        seed_mgr = MagicMock()
        handoff = DiscoveryHandoff.failed("rustmapper crash", discovery_source="rustmapper")
        with pytest.raises(DiscoveryHandoffError):
            ingest_handoff_via_seed_manager(
                seed_mgr,
                handoff,
                source_url="https://example.edu/sitemap.xml",
            )
        seed_mgr.add_urls_to_seeds.assert_not_called()


class TestConfigModes:
    def test_config_documents_shared_dedup_default(self):
        class FakeConfig:
            def get(self, key, default=None):
                if key in ("stage1.discovery", "stages.stage1.discovery"):
                    return {
                        "mode": "shared_dedup",
                        "claim_ttl_seconds": 3600,
                        "job_id": "cfg-job",
                        "ack_empty": False,
                    }
                return default

        dedup = get_discovery_dedup_from_config(FakeConfig(), job_id=None)
        assert dedup.mode == DiscoveryDedupMode.SHARED_DEDUP
        assert dedup.job_id == "cfg-job"
        assert dedup.ttl_seconds == 3600

    def test_config_mutex_mode(self):
        class FakeConfig:
            def get(self, key, default=None):
                if key in ("stage1.discovery", "stages.stage1.discovery"):
                    return {"mode": "mutex", "job_id": "m1"}
                return default

        dedup = get_discovery_dedup_from_config(FakeConfig())
        assert dedup.mode == DiscoveryDedupMode.MUTEX
