"""Tests for Stage 3 storage backends."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from stage3.storage import (  # noqa: E402
    CompressionConfig,
    ParquetStorageWriter,
    RotationPolicy,
)

def make_enrichment_item(url_suffix: str) -> dict:
    """Create a sample enrichment item for testing."""
    return {
        "url": f"https://example.com/{url_suffix}",
        "url_hash": f"hash-{url_suffix}",
        "title": "Title",
        "text_content": "Sample body",
    }


def test_parquet_writer_compression_extension(tmp_path):
    """Parquet writer should not append a compression suffix to the filename."""
    pytest.importorskip("pyarrow")
    output_path = tmp_path / "test.parquet"
    writer = ParquetStorageWriter(
        path=output_path,
        rotation=RotationPolicy(max_items=1),
        compression=CompressionConfig(codec="gzip"),
    )
    writer.open()
    writer.write_item(make_enrichment_item("one"))
    writer.close()

    # The bug is that the writer creates "test.parquet.gz" instead of "test.parquet".
    # The test will fail until the bug is fixed.
    assert output_path.exists()
    assert not (tmp_path / "test.parquet.gz").exists()

    # Verify content to be sure
    import pyarrow.parquet as pq
    table = pq.read_table(output_path)
    assert table.num_rows == 1
    assert table.column("url_hash").to_pylist() == ["hash-one"]