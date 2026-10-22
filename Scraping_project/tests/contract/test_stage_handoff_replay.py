"""Offline replay of recorded stage-boundary handoff fixtures (#686).

Each boundary has recorded input under ``tests/fixtures/stage_handoffs/``. The
tests feed those files through the real consumers in ``src/`` with no Redis,
Kafka, Postgres or lakehouse. Valid fixtures pin the expected output; malformed
and legacy records pin the documented error handling.
"""

import asyncio
import json
from pathlib import Path

import pytest

from src.core.ingest_contract import missing_ingest_fields
from src.stage1.discovery_handoff import (
    DiscoveryEmptyNeedsAck,
    DiscoveryHandoffError,
    DiscoveryStatus,
    checksum_urls,
    consume_rustmapper_handoff,
    load_sitemap_jsonl,
    read_handoff_json,
    require_ingestible,
)
from src.stage3.stage3_worker import FingerprintStore, Stage3Worker
from src.stage4.stage4_worker import ANALYSIS_TABLE, QUEUE_TABLE, Stage4Worker
from src.utils.kafka_poison_policy import DLQ_AND_COMMIT, RETRY, PoisonDecision, decide

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "stage_handoffs"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --------------------------------------------------------------------------
# Stage 1 -> Stage 2: discovery handoff (sitemap.jsonl + handoff sidecar)
# --------------------------------------------------------------------------

S1_TO_S2 = FIXTURES / "stage1_to_stage2"

# Stable output of sitemap_mixed.jsonl: legacy bare-URL lines, ``loc`` keys,
# whitespace padding and one duplicate kept in order; junk lines dropped.
MIXED_URLS = [
    "https://www.uconn.edu/",
    "https://www.uconn.edu/admissions/",
    "https://www.uconn.edu/academics/",
    "https://www.uconn.edu/academics/",
    "https://www.uconn.edu/research/",
    "https://www.uconn.edu/news/",
]
# Stable output of handoff_success.json (no checksum recorded, so it is derived).
SIDECAR_URLS = [
    "https://www.uconn.edu/",
    "https://www.uconn.edu/academics/",
    "https://www.uconn.edu/admissions/",
    "https://www.uconn.edu/news/",
    "https://www.uconn.edu/research/",
]


@pytest.mark.contract
@pytest.mark.stage1
class TestStage1ToStage2Replay:
    def test_mixed_sitemap_yields_stable_urls(self):
        urls, file_checksum = load_sitemap_jsonl(S1_TO_S2 / "sitemap_mixed.jsonl")

        assert urls == MIXED_URLS
        assert len(file_checksum) == 64

    def test_mixed_sitemap_is_success_with_payload_checksum(self):
        handoff = consume_rustmapper_handoff(S1_TO_S2 / "sitemap_mixed.jsonl")

        assert handoff.status == DiscoveryStatus.SUCCESS
        assert handoff.discovered == MIXED_URLS
        assert handoff.checksum == checksum_urls(MIXED_URLS)
        assert require_ingestible(handoff) == MIXED_URLS

    def test_blank_sitemap_is_empty_not_failed(self):
        handoff = consume_rustmapper_handoff(S1_TO_S2 / "sitemap_empty.jsonl")

        assert handoff.status == DiscoveryStatus.EMPTY
        with pytest.raises(DiscoveryEmptyNeedsAck):
            require_ingestible(handoff)
        assert require_ingestible(handoff, ack_empty=True) == []

    def test_missing_sitemap_fails_closed(self, tmp_path):
        handoff = consume_rustmapper_handoff(tmp_path / "absent.jsonl")

        assert handoff.is_failed
        assert "sitemap.jsonl missing" in handoff.error
        with pytest.raises(DiscoveryHandoffError, match="discovery failed"):
            require_ingestible(handoff)

    def test_success_sidecar_without_checksum_derives_one_and_verifies(self):
        handoff = read_handoff_json(S1_TO_S2 / "handoff_success.json")

        assert handoff.is_success
        assert handoff.checksum == checksum_urls(SIDECAR_URLS)
        assert require_ingestible(handoff) == SIDECAR_URLS

    def test_success_sidecar_with_wrong_checksum_is_refused(self):
        handoff = read_handoff_json(S1_TO_S2 / "handoff_success_bad_checksum.json")

        with pytest.raises(DiscoveryHandoffError, match="checksum mismatch"):
            require_ingestible(handoff)

    def test_failed_sidecar_is_returned_without_reparsing_the_sitemap(self):
        handoff = consume_rustmapper_handoff(
            S1_TO_S2 / "sitemap_mixed.jsonl", S1_TO_S2 / "handoff_failed.json"
        )

        assert handoff.is_failed
        assert handoff.error == "rustmapper exited with status 137 (killed)"
        with pytest.raises(DiscoveryHandoffError, match="status 137"):
            require_ingestible(handoff)

    def test_empty_sidecar_is_returned_without_reparsing_the_sitemap(self):
        handoff = consume_rustmapper_handoff(
            S1_TO_S2 / "sitemap_mixed.jsonl", S1_TO_S2 / "handoff_empty.json"
        )

        assert handoff.is_empty
        assert require_ingestible(handoff, ack_empty=True) == []


# --------------------------------------------------------------------------
# Stage 1 -> Kafka -> kafka-delta-ingest: scraped item records
# --------------------------------------------------------------------------

S1_TO_INGEST = FIXTURES / "stage1_to_ingest" / "scraped_items.jsonl"

# One entry per valid-JSON line, in file order: fields the Rust ingestor would
# reject. Line 3 is a legacy ``spider`` key; line 5 an epoch int timestamp;
# line 6 a whitespace-only spider name; line 7 a null url plus an empty spider.
EXPECTED_MISSING = [
    [],
    [],
    ["spider_name"],
    ["url"],
    ["scraped_at_utc"],
    ["spider_name"],
    ["url", "spider_name"],
]


@pytest.mark.contract
@pytest.mark.stage1
class TestStage1ToIngestReplay:
    def test_records_match_ingest_contract_in_file_order(self):
        *records, _malformed = S1_TO_INGEST.read_text(encoding="utf-8").splitlines()

        assert [missing_ingest_fields(json.loads(line)) for line in records] == EXPECTED_MISSING

    def test_truncated_record_is_not_valid_json(self):
        *_, malformed = S1_TO_INGEST.read_text(encoding="utf-8").splitlines()

        with pytest.raises(json.JSONDecodeError):
            json.loads(malformed)

    def test_poison_policy_retries_then_dead_letters_malformed_record(self):
        assert decide(1, 3) == PoisonDecision(RETRY, commit_offset=False, send_to_dlq=False)
        assert decide(3, 3) == PoisonDecision(DLQ_AND_COMMIT, commit_offset=True, send_to_dlq=True)


# --------------------------------------------------------------------------
# Stage 2 -> Stage 3: stage2_page_analysis rows feeding MinHash dedup
# --------------------------------------------------------------------------

S2_TO_S3 = FIXTURES / "stage2_to_stage3" / "stage2_page_analysis.jsonl"


@pytest.mark.contract
@pytest.mark.stage3
class TestStage2ToStage3Replay:
    def test_dedup_keeps_first_copy_of_each_document(self):
        worker = Stage3Worker.__new__(Stage3Worker)
        worker._fingerprints = FingerprintStore(threshold=0.5, redis_client=None)

        kept = asyncio.run(worker._deduplicate_documents(_jsonl(S2_TO_S3)))

        # Identical text under a new URL, a repeated url_hash, an empty body and
        # a legacy row without text_content are all dropped.
        assert [doc["url_hash"] for doc in kept] == ["h-academics", "h-research"]


# --------------------------------------------------------------------------
# Stage 2 -> Stage 4: stage4_large_docs queue and massive-doc analysis fallback
# --------------------------------------------------------------------------

S2_TO_S4 = FIXTURES / "stage2_to_stage4"
ANNUAL = "https://www.uconn.edu/annual-report/"
BUDGET = "https://www.uconn.edu/budget.pdf"
LEGACY = "https://www.uconn.edu/legacy-doc/"
SENATE = "https://www.uconn.edu/senate-minutes/"
ALREADY = "https://www.uconn.edu/already-summarized/"


def _stage4_worker(queue_rows=(), analysis_rows=(), analysis_fallback=True):
    worker = Stage4Worker.__new__(Stage4Worker)
    worker.delta = None
    worker.analysis_fallback = analysis_fallback
    tables = {QUEUE_TABLE: list(queue_rows), ANALYSIS_TABLE: list(analysis_rows)}
    worker._read = lambda table, columns=None: list(tables.get(table, []))
    return worker


@pytest.mark.contract
@pytest.mark.stage4
class TestStage2ToStage4Replay:
    def test_pending_queue_rows_are_the_primary_input(self):
        worker = _stage4_worker(queue_rows=_jsonl(S2_TO_S4 / "stage4_large_docs.jsonl"))

        pending, queued = worker._pending_from_queue(done_urls=set())

        # The duplicate ANNUAL row collapses to its first pending copy; the
        # settled BUDGET row and the url-less orphan are not pending.
        assert [row["url"] for row in pending] == [ANNUAL, LEGACY, SENATE, ALREADY]
        assert queued == {ANNUAL, BUDGET, LEGACY, SENATE, ALREADY}

    def test_urls_already_done_elsewhere_are_not_pending(self):
        worker = _stage4_worker(queue_rows=_jsonl(S2_TO_S4 / "stage4_large_docs.jsonl"))

        pending, _ = worker._pending_from_queue(done_urls={ALREADY})

        assert [row["url"] for row in pending] == [ANNUAL, LEGACY, SENATE]

    def test_stored_text_is_used_only_when_complete(self):
        rows = {row["url_hash"]: row for row in _jsonl(S2_TO_S4 / "stage4_large_docs.jsonl")}

        assert Stage4Worker._stored_text(rows["h-annual"]) == "Full document body"
        # A legacy row without ``status`` still carries complete text.
        assert Stage4Worker._stored_text(rows["h-legacy"]) == "Full document body"
        # Truncated prefix (shorter than content_length) must be re-fetched.
        assert Stage4Worker._stored_text(rows["h-senate"]) is None
        assert Stage4Worker._stored_text(rows["h-budget"]) is None

    def test_analysis_fallback_reads_only_unrouted_massive_docs(self):
        worker = _stage4_worker(analysis_rows=_jsonl(S2_TO_S4 / "stage2_page_analysis.jsonl"))

        docs = worker._fallback_from_analysis(exclude={SENATE})

        # Excluded: non-massive rows, the pre-routing row without is_massive_doc,
        # the failed row, the excluded routed URL and the row with no url.
        assert [doc["url"] for doc in docs] == [ANNUAL]

    def test_analysis_fallback_can_be_disabled(self):
        worker = _stage4_worker(
            analysis_rows=_jsonl(S2_TO_S4 / "stage2_page_analysis.jsonl"),
            analysis_fallback=False,
        )

        assert worker._fallback_from_analysis(exclude=set()) == []
