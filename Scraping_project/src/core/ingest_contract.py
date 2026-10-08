"""Field contract between the Python Kafka producer and the Rust ingestor (#531).

``kafka-delta-ingest`` rejects (never empty-fills) a message whose required fields
are missing, empty or not strings.  ``REQUIRED_INGEST_FIELDS`` must match the
Rust ``REQUIRED_INGEST_FIELDS`` constant; ``tests/unit/test_ingest_field_contract.py``
fails if the two drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

REQUIRED_INGEST_FIELDS: tuple[str, ...] = ("url", "scraped_at_utc", "spider_name")
OPTIONAL_INGEST_FIELDS: tuple[str, ...] = ("title", "content", "pipeline_version")


def missing_ingest_fields(record: Mapping[str, Any]) -> list[str]:
    """Required fields the Rust ingestor would reject this record for."""
    return [
        field
        for field in REQUIRED_INGEST_FIELDS
        if not isinstance(record.get(field), str) or not record[field].strip()
    ]
