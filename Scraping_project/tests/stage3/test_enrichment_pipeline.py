"""Tests for Stage 3 enrichment pipeline."""

from __future__ import annotations

import gzip
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from common.schemas import EnrichmentItem  # noqa: E402
from stage3.enrichment_pipeline import Stage3Pipeline  # noqa: E402


class DummySpider:
    name = "enrichment-spider"


def make_item(url_suffix: str) -> EnrichmentItem:
    return EnrichmentItem(
        url=f'https://example.com/{url_suffix}',
        url_hash=f'hash-{url_suffix}',
        title='Title',
        text_content='Sample body',
        word_count=2,
        entities=[],
        keywords=[],
        content_tags=[],
        has_pdf_links=False,
        has_audio_links=False,
        status_code=200,
        content_type='text/html',
        enriched_at='2024-01-01T00:00:00',
    )



FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "sample_responses.json"
SAMPLE_RESPONSES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("count", [1, 2])
def test_stage3_pipeline_writes_items(tmp_path, count):
    """Pipeline should persist every enriched item while preserving unique hashes."""
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
    """Pipeline must create the output directory hierarchy on open."""
    output = tmp_path / "nested" / "enriched.jsonl"
    pipeline = Stage3Pipeline(output_file=str(output))
    pipeline.open_spider(DummySpider())
    assert output.parent.exists()
    pipeline.close_spider(DummySpider())


def test_stage3_pipeline_returns_original_item(tmp_path):
    """process_item should never mutate or replace the original item instance."""
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


def test_stage3_pipeline_preserves_metadata(tmp_path):
    """Written payload should retain enrichment metadata supplied by the item."""
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


def test_stage3_pipeline_jsonl_rotation(tmp_path):
    storage_config = {
        "backend": "jsonl",
        "options": {"path": str(tmp_path / "enriched.jsonl")},
        "rotation": {"max_items": 1},
    }
    pipeline = Stage3Pipeline(storage_config=storage_config)
    spider = DummySpider()
    pipeline.open_spider(spider)

    pipeline.process_item(make_item("one"), spider)
    pipeline.process_item(make_item("two"), spider)
    pipeline.close_spider(spider)

    jsonl_files = sorted(tmp_path.glob("enriched*.jsonl*"))
    assert len(jsonl_files) >= 2

    payloads = []
    for file_path in jsonl_files:
        if file_path.suffix == ".gz" or file_path.name.endswith(".gz"):
            with gzip.open(file_path, 'rt', encoding='utf-8') as handle:
                payloads.extend(json.loads(line) for line in handle if line.strip())
        else:
            payloads.extend(json.loads(line) for line in file_path.read_text(encoding='utf-8').splitlines() if line.strip())

    assert {entry["url_hash"] for entry in payloads} == {"hash-one", "hash-two"}


def test_stage3_pipeline_sqlite_backend(tmp_path):
    db_path = tmp_path / "enriched.db"
    storage_config = {
        "backend": "sqlite",
        "options": {"path": str(db_path)},
    }
    pipeline = Stage3Pipeline(storage_config=storage_config)
    spider = DummySpider()
    pipeline.open_spider(spider)

    pipeline.process_item(make_item("sqlite"), spider)
    pipeline.close_spider(spider)

    assert db_path.exists()
    conn = sqlite3.connect(db_path)
    try:
        rows = list(conn.execute("SELECT url, url_hash, payload FROM enrichment_items"))
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0][1] == "hash-sqlite"
    stored = json.loads(rows[0][2])
    assert stored["url"] == "https://example.com/sqlite"


def test_stage3_pipeline_parquet_backend(tmp_path):
    pytest.importorskip("pyarrow")
    parquet_path = tmp_path / "enriched.parquet"
    storage_config = {
        "backend": "parquet",
        "options": {"path": str(parquet_path), "batch_size": 1},
    }
    pipeline = Stage3Pipeline(storage_config=storage_config)
    spider = DummySpider()
    pipeline.open_spider(spider)

    pipeline.process_item(make_item("parquet"), spider)
    pipeline.close_spider(spider)

    import pyarrow.parquet as pq  # type: ignore

    table = pq.read_table(parquet_path)
    assert table.num_rows == 1
    column = table.column("url_hash").to_pylist()
    assert column == ["hash-parquet"]


def test_stage3_pipeline_s3_backend(tmp_path):
    storage_config = {
        "backend": "s3",
        "options": {
            "bucket": "unit-test",
            "prefix": "stage3/",
            "base_name": "sample",
        },
        "rotation": {"max_items": 1},
        "compression": {"codec": "gzip"},
    }

    fake_client = MagicMock()
    fake_client.put_object = MagicMock()

    fake_session = MagicMock()
    fake_session.client.return_value = fake_client

    fake_module = MagicMock()
    fake_module.Session.return_value = fake_session

    with patch.dict(sys.modules, {"boto3": fake_module}):
        pipeline = Stage3Pipeline(storage_config=storage_config)
        spider = DummySpider()
        pipeline.open_spider(spider)
        pipeline.process_item(make_item("s3-1"), spider)
        pipeline.process_item(make_item("s3-2"), spider)
        pipeline.close_spider(spider)

    assert fake_client.put_object.call_count == 2
    uploaded_keys = [call.kwargs["Key"] for call in fake_client.put_object.call_args_list]
    for key in uploaded_keys:
        assert key.startswith("stage3/sample-")
        assert key.endswith(".jsonl.gz")

    bodies = []
    for call in fake_client.put_object.call_args_list:
        body = call.kwargs["Body"]
        decompressed = gzip.decompress(body).decode('utf-8')
        bodies.extend(json.loads(line) for line in decompressed.splitlines() if line.strip())

    assert {entry["url_hash"] for entry in bodies} == {"hash-s3-1", "hash-s3-2"}
