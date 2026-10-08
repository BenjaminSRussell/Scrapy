"""Unit suite for ``src/pipelines.py`` (#246): one class per pipeline class.

Offline: Delta is a ``FakeDelta`` recorder, Kafka producers are fakes, and
no Redis is needed. Each class asserts the drop vs pass-through contract.
Deeper behaviour already has focused suites (Kafka spill #175/#249,
summary fail-soft #462, queue registration #608, bronze contract #227); this
file is the per-class map of what each pipeline does to an item.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from scrapy.exceptions import DropItem

from src import pipelines as P
from src.items import OffsiteCandidateItem

SPIDER = SimpleNamespace(name="scout", crawler=None)


class FakeDelta:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.writes: list[tuple[str, list[dict[str, Any]]]] = []

    def write(self, table, rows, mode="append", **kwargs):
        self.writes.append((table, list(rows)))
        return self.ok


def _offsite(**overrides):
    data = {
        "source_page": "https://example.com/a",
        "external_url": "https://partner.org/x",
        "anchor_text": "Partner",
        "context": "see partner",
        "discovered_at": "2026-01-01T00:00:00",
    }
    data.update(overrides)
    return OffsiteCandidateItem(**data)


class TestDataValidationPipeline:
    def test_passes_valid_item(self):
        p = P.DataValidationPipeline()
        item = {"url": "https://example.com/"}
        assert p.process_item(item, SPIDER) is item
        assert p.items_validated == 1

    @pytest.mark.parametrize("bad", [{}, {"url": ""}, {"url": "   "}, {"url": None}])
    def test_drops_missing_or_blank_required(self, bad):
        p = P.DataValidationPipeline()
        with pytest.raises(DropItem):
            p.process_item(bad, SPIDER)
        assert p.items_dropped == 1

    def test_offsite_items_require_external_url_not_url(self):
        p = P.DataValidationPipeline(required_fields=["url"])
        item = _offsite()
        assert p.process_item(item, SPIDER) is item
        with pytest.raises(DropItem):
            p.process_item(_offsite(external_url=""), SPIDER)


class TestDataCleansingPipeline:
    def test_strips_lowercases_and_parses_currency(self):
        p = P.DataCleansingPipeline()
        item = {"title": "  Hello  ", "status": " OPEN ", "price": "$1,234.50", "tags": [" a ", "b ", 3], "n": None}
        out = p.process_item(item, SPIDER)
        assert out is item
        assert item == {"title": "Hello", "status": "open", "price": 1234.5, "tags": ["a", "b", 3], "n": None}

    def test_unparseable_currency_is_kept(self):
        p = P.DataCleansingPipeline()
        item = {"price": "call us"}
        assert p.process_item(item, SPIDER)["price"] == "call us"


class TestMetadataPipeline:
    def test_stamps_provenance(self):
        p = P.MetadataPipeline()
        item = {"url": "https://example.com/"}
        out = p.process_item(item, SPIDER)
        assert out is item
        assert item["spider_name"] == "scout"
        assert item["pipeline_version"] == P.MetadataPipeline.PIPELINE_VERSION
        assert item["scraped_at_utc"].endswith(("Z", "+00:00"))


class _Producer:
    def __init__(self, fail_times: int = 0):
        self.fail_times = fail_times
        self.produced: list[dict[str, Any]] = []

    def produce(self, topic, key=None, value=None, callback=None):
        if self.fail_times:
            self.fail_times -= 1
            raise BufferError("queue full")
        self.produced.append({"topic": topic, "key": key, "value": value})

    def poll(self, timeout=0):
        return 0

    def flush(self, timeout=None):
        return 0


class TestKafkaPipeline:
    def _pipeline(self, tmp_path, producer):
        p = P.KafkaPipeline("localhost:9092", "items", spill_dir=tmp_path / "spill", retry_backoff=0)
        p.producer = producer
        return p

    def test_routing_items_pass_through_unpublished(self, tmp_path):
        prod = _Producer()
        p = self._pipeline(tmp_path, prod)
        item = {"url": "https://example.com/", "target_stage": "stage2"}
        assert p.process_item(item, SPIDER) is item
        assert prod.produced == []

    def test_publishes_keyed_message(self, tmp_path):
        prod = _Producer()
        p = self._pipeline(tmp_path, prod)
        item = {"url": "https://example.com/", "url_hash": "abc"}
        assert p.process_item(item, SPIDER) is item
        assert len(prod.produced) == 1 and prod.produced[0]["topic"] == "items"
        assert prod.produced[0]["key"] is not None

    def test_retries_then_spills_instead_of_dropping(self, tmp_path):
        prod = _Producer(fail_times=99)
        p = self._pipeline(tmp_path, prod)
        item = {"url": "https://example.com/", "url_hash": "abc"}
        assert p.process_item(item, SPIDER) is item  # durably spilled, item flows on
        assert p.messages_spilled == 1
        assert list((tmp_path / "spill").glob("*"))

    def test_drops_when_producer_missing(self, tmp_path):
        p = self._pipeline(tmp_path, None)
        with pytest.raises(DropItem):
            p.process_item({"url": "https://example.com/"}, SPIDER)


class TestBufferedDeltaBatch:
    def test_flushes_on_row_count_and_clears(self):
        delta = FakeDelta()
        b = P.BufferedDeltaBatch(delta, "t", max_rows=2, max_age=0)
        assert b.add({"a": 1}) is False
        assert b.add({"a": 2}) is True
        assert delta.writes == [("t", [{"a": 1}, {"a": 2}])]
        assert len(b) == 0 and b.rows_written == 2

    def test_flushes_on_age_with_injected_clock(self):
        now = [0.0]
        delta = FakeDelta()
        b = P.BufferedDeltaBatch(delta, "t", max_rows=100, max_age=5, clock=lambda: now[0])
        b.add({"a": 1})
        assert b.flush_if_due() is False
        now[0] = 6.0
        assert b.flush_if_due() is True
        assert delta.writes == [("t", [{"a": 1}])]

    def test_failed_write_is_counted_not_retained(self):
        delta = FakeDelta(ok=False)
        b = P.BufferedDeltaBatch(delta, "t", max_rows=1, max_age=0)
        assert b.add({"a": 1}) is False
        assert len(b) == 0 and b.rows_unwritten == 1 and b.rows_written == 0


@pytest.fixture
def fake_delta(monkeypatch):
    delta = FakeDelta()
    import src.utils.delta as delta_mod

    monkeypatch.setattr(delta_mod, "get_delta", lambda *a, **k: delta)
    return delta


class TestQueueItemPipeline:
    def test_routes_js_and_stage2_and_flushes_on_close(self, fake_delta):
        p = P.QueueItemPipeline({"max_rows": 100, "max_age": 0})
        js = {"url": "https://example.com/a/", "target_spider": "javascript"}
        s2 = {"url": "https://example.com/b?utm_source=x", "target_stage": "stage2"}
        content = {"url": "https://example.com/c", "title": "content record"}
        for item in (js, s2, content):
            assert p.process_item(item, SPIDER) is item  # always passes through
        p.spider_closed(SPIDER)
        tables = {t: rows for t, rows in fake_delta.writes}
        assert set(tables) == {"js_spider_queue", "stage2_queue"}
        assert len(tables["js_spider_queue"]) == 1 and len(tables["stage2_queue"]) == 1
        assert "utm_source" not in tables["stage2_queue"][0]["url"]  # canonicalised (#728)
        assert tables["stage2_queue"][0]["url_hash"]

    def test_ssrf_targets_are_not_queued(self, fake_delta):
        p = P.QueueItemPipeline({"max_rows": 100, "max_age": 0})
        item = {"url": "http://169.254.169.254/latest/meta-data/", "target_stage": "stage2"}
        assert p.process_item(item, SPIDER) is item
        p.spider_closed(SPIDER)
        assert fake_delta.writes == []


class TestOffsiteCandidatePipeline:
    def test_batches_offsite_and_ignores_others(self, fake_delta):
        p = P.OffsiteCandidatePipeline({"max_rows": 100, "max_age": 0})
        other = {"url": "https://example.com/"}
        assert p.process_item(other, SPIDER) is other
        item = _offsite()
        assert p.process_item(item, SPIDER) is item
        p.spider_closed(SPIDER)
        assert fake_delta.writes and fake_delta.writes[0][0] == "stage1_offsite_candidates"
        assert fake_delta.writes[0][1][0]["external_url"] == "https://partner.org/x"

    def test_drops_incomplete_offsite(self, fake_delta):
        p = P.OffsiteCandidatePipeline({"max_rows": 100, "max_age": 0})
        with pytest.raises(DropItem):
            p.process_item(OffsiteCandidateItem(external_url="https://x.org"), SPIDER)


class TestGrafanaSummaryPipeline:
    def test_never_drops_and_skips_offsite(self, monkeypatch):
        p = P.GrafanaSummaryPipeline()
        monkeypatch.setattr(p, "_sample", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
        p.items_processed = p.SAMPLE_RATE - 1
        item = {"url": "https://example.com/", "text": "hello"}
        assert p.process_item(item, SPIDER) is item  # sampling failure is a skip
        off = _offsite()
        assert p.process_item(off, SPIDER) is off


class TestSchemaValidationPipeline:
    def test_valid_bronze_record_passes_and_is_stamped(self):
        p = P.SchemaValidationPipeline()
        item = {"url": "https://example.com/a", "title": "A"}
        out = p.process_item(item, SPIDER)
        assert out is item
        assert item["spider_name"] == "scout" and item["scraped_at_utc"]
        assert item["validation_status"] is True

    def test_invalid_record_dropped_and_failure_published(self):
        published = []

        class Prod:
            def produce(self, topic, value):
                published.append(topic)

            def poll(self, t):
                return 0

        p = P.SchemaValidationPipeline()
        p.kafka_producer = Prod()
        with pytest.raises(DropItem):
            p.process_item({"title": "no url"}, SPIDER)  # url is bronze-required
        assert p.items_dropped == 1
        assert published == ["validation_failures"]

    def test_routing_and_offsite_and_disabled_pass_through(self):
        p = P.SchemaValidationPipeline()
        routing = {"url": "nope", "target_stage": "stage2"}
        assert p.process_item(routing, SPIDER) is routing
        off = _offsite()
        assert p.process_item(off, SPIDER) is off
        disabled = P.SchemaValidationPipeline(enabled=False)
        bad = {"url": "nope"}
        assert disabled.process_item(bad, SPIDER) is bad


class TestRecencyScoringPipeline:
    def test_scores_dated_and_defaults_undated(self):
        p = P.RecencyScoringPipeline(decay_constant=0.01, default_score=0.42)
        dated = {"url": "u", "publication_date": "2026-01-01T00:00:00Z"}
        undated = {"url": "u"}
        assert p.process_item(dated, SPIDER) is dated
        assert 0.0 <= dated["recency_score"] <= 1.0
        assert p.process_item(undated, SPIDER)["recency_score"] == 0.42

    def test_bad_date_falls_back_to_default(self):
        p = P.RecencyScoringPipeline(default_score=0.5)
        item = {"url": "u", "publication_date": "not a date"}
        assert p.process_item(item, SPIDER)["recency_score"] == 0.5

    def test_offsite_untouched(self):
        p = P.RecencyScoringPipeline()
        off = _offsite()
        p.process_item(off, SPIDER)
        assert "recency_score" not in off


class TestAggregationPipeline:
    def test_groups_by_entity_and_persists_on_close(self):
        delta = FakeDelta()
        p = P.AggregationPipeline(delta=delta, max_items_per_entity=2)
        for i, score in enumerate([0.1, 0.9, 0.5]):
            p.process_item({"entity_id": "dept", "url": f"https://e.com/{i}", "recency_score": score}, SPIDER)
        no_entity = {"url": "https://e.com/x"}
        assert p.process_item(no_entity, SPIDER) is no_entity
        assert len(p.entity_groups["dept"]) == 2  # bounded per entity
        p.close_spider(SPIDER)
        (table, rows), = delta.writes
        assert table == "entity_summaries" and len(rows) == 1
        assert rows[0]["entity_id"] == "dept" and rows[0]["source_count"] == 3
        assert rows[0]["max_recency_score"] == 0.9

    def test_persist_failure_never_raises(self):
        class Boom:
            def write(self, *a, **k):
                raise OSError("disk")

        p = P.AggregationPipeline(delta=Boom())
        p.process_item({"entity_id": "x", "url": "u"}, SPIDER)
        p.close_spider(SPIDER)  # logged, not raised
        assert p.summaries_written == 0


class TestMetadataExtractionPipeline:
    def test_simple_extractor_adds_keywords_and_batches(self, monkeypatch):
        p = P.MetadataExtractionPipeline(extractor_type="none", batch_size=100)
        saved = []
        monkeypatch.setattr(p, "_save_batch", lambda: saved.append(list(p.batch)))
        item = {"url": "https://e.com/", "content": "research research grants grants grants faculty"}
        assert p.process_item(item, SPIDER) is item
        assert item["extracted_metadata"]["keywords"][0] == "grants"
        assert len(p.batch) == 1 and saved == []

    def test_items_without_text_pass_untouched(self):
        p = P.MetadataExtractionPipeline(extractor_type="none")
        item = {"url": "https://e.com/"}
        assert p.process_item(item, SPIDER) is item
        assert "extracted_metadata" not in item
