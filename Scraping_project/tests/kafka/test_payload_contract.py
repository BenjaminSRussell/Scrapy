"""Kafka payload contract, offline (#281).

``tests/fixtures/kafka/`` holds the wire shape of the two payloads the Python
producers send: the Stage 1 item on ``scraped-items`` (read by kafka-delta-ingest)
and the ``ValidationFailureRecord`` on ``validation_failures``. These checks need
no broker, so they run in the default CI selection. The produce -> consume round
trip through the real pipelines is in ``test_payload_roundtrip.py`` (``kafka``).
"""

import json
import re
from pathlib import Path

from src.core.ingest_contract import REQUIRED_INGEST_FIELDS, missing_ingest_fields
from src.schemas import ValidationFailureRecord

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "kafka"
MAIN_RS = ROOT / "kafka-delta-ingest" / "src" / "main.rs"


def _golden(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _rust_timestamp_pattern() -> str:
    """The ``scraped_at_utc`` pattern kafka-delta-ingest validates against."""
    match = re.search(
        r'"scraped_at_utc": \{\s*"type": "string",\s*"pattern": "([^"]+)"',
        MAIN_RS.read_text(encoding="utf-8"),
    )
    assert match, "scraped_at_utc pattern not found in kafka-delta-ingest/src/main.rs"
    return match.group(1).replace("\\\\", "\\")


def test_golden_scraped_item_has_every_ingest_required_field():
    golden = _golden("scraped_item.json")
    assert set(REQUIRED_INGEST_FIELDS) <= set(golden)
    assert missing_ingest_fields(golden) == []


def test_golden_scraped_item_timestamp_matches_the_rust_pattern():
    golden = _golden("scraped_item.json")
    assert re.match(_rust_timestamp_pattern(), golden["scraped_at_utc"])


def test_golden_scraped_item_optional_fields_are_string_or_null():
    golden = _golden("scraped_item.json")
    for name in ("title", "content", "pipeline_version"):
        assert golden[name] is None or isinstance(golden[name], str), name


def test_golden_validation_failure_parses_as_its_model():
    golden = _golden("validation_failure.json")
    record = ValidationFailureRecord.model_validate(golden)
    assert record.field_name == golden["field_name"]
    required = {name for name, field in ValidationFailureRecord.model_fields.items() if field.is_required()}
    assert required <= set(golden), sorted(required - set(golden))
    for name in required:
        assert isinstance(golden[name], str) and golden[name].strip(), name
