"""Tests for Stage 3 enrichment pipeline."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from common.schemas import EnrichmentItem
from stage3.enrichment_pipeline import Stage3Pipeline


class DummySpider:
    name = "enrichment-spider"


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "sample_responses.json"
SAMPLE_RESPONSES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("count", [1, 2])
def test_stage3_pipeline_writes_items(tmp_path, count):
    output = tmp_path / "enriched.jsonl"
    pipeline = Stage3Pipeline(output_file=str(output))
    pipeline.open_spider(DummySpider())

    if len(SAMPLE_RESPONSES) < count:
        pytest.skip("Not enough sample responses to satisfy count")

    for idx in range(count):
        sample = SAMPLE_RESPONSES[idx]
        item = EnrichmentItem(
            url=sample["url"],
            url_hash=f"fixture_hash_{idx}",
            title=sample.get("title", ""),
            text_content=sample.get("text_content", ""),
            word_count=len(sample.get("text_content", "").split()),
            entities=sample.get("entities", []),
            keywords=sample.get("keywords", []),
            content_tags=sample.get("content_tags", []),
            has_pdf_links=any(".pdf" in link.lower() for link in sample.get("links", [])),
            has_audio_links=any(link.lower().endswith(".mp3") for link in sample.get("links", [])),
            status_code=sample.get("status_code", 200),
            content_type=sample.get("content_type", "text/html"),
            enriched_at=f"2024-01-01T00:00:{idx:02d}",
        )
        pipeline.process_item(item, DummySpider())

    pipeline.close_spider(DummySpider())

    lines = output.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == count
    payloads = [json.loads(line) for line in lines]
    assert {entry["url_hash"] for entry in payloads} == {f"fixture_hash_{idx}" for idx in range(count)}
    assert all("processed_at" in entry for entry in payloads)


def test_stage3_pipeline_directory_creation(tmp_path):
    output = tmp_path / "nested" / "enriched.jsonl"
    pipeline = Stage3Pipeline(output_file=str(output))
    pipeline.open_spider(DummySpider())
    assert output.parent.exists()
    pipeline.close_spider(DummySpider())


def test_stage3_pipeline_returns_original_item(tmp_path):
    pipeline = Stage3Pipeline(output_file=str(tmp_path / "enriched.jsonl"))
    pipeline.open_spider(DummySpider())
    sample_item = EnrichmentItem(
        url="https://uconn.edu/ret",
        url_hash="hash_ret",
        title="Return Test",
        text_content="Return test body",
        word_count=3,
        entities=[],
        keywords=[],
        content_tags=[],
        has_pdf_links=False,
        has_audio_links=False,
        status_code=200,
        content_type="text/plain",
        enriched_at="2024-01-01T00:00:00",
    )
    returned = pipeline.process_item(sample_item, DummySpider())
    assert returned is sample_item
    pipeline.close_spider(DummySpider())


@pytest.mark.xfail(reason="Stage3 pipeline drops original enrichment metadata", strict=True)
def test_stage3_pipeline_preserves_metadata(tmp_path):
    output = tmp_path / "enriched.jsonl"
    pipeline = Stage3Pipeline(output_file=str(output))
    pipeline.open_spider(DummySpider())

    sample = SAMPLE_RESPONSES[0]
    item = EnrichmentItem(
        url=sample["url"],
        url_hash="fixture_hash_metadata",
        title=sample.get("title", ""),
        text_content=sample.get("text_content", ""),
        word_count=len(sample.get("text_content", "").split()),
        entities=sample.get("entities", []),
        keywords=sample.get("keywords", []),
        content_tags=sample.get("content_tags", []),
        has_pdf_links=False,
        has_audio_links=False,
        status_code=sample.get("status_code", 200),
        content_type=sample.get("content_type", "text/html"),
        enriched_at="2024-01-01T00:00:00",
    )

    pipeline.process_item(item, DummySpider())
    pipeline.close_spider(DummySpider())

    payload = json.loads(output.read_text(encoding="utf-8").strip())
    assert payload.get("processed_at") is not None
    assert payload.get("enriched_at") == item.enriched_at
    assert payload.get("status_code") == item.status_code
