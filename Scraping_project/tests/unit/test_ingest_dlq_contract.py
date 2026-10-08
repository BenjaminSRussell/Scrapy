"""#544: the Rust ingestor dead-letters rejected messages instead of dropping them.

The Rust behaviour itself is covered by `cargo test` in kafka-delta-ingest/
(including a librdkafka MockCluster round-trip and a fail-closed test). This
contract test keeps the Python-side config and the binary in agreement.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MAIN_RS = (ROOT / "kafka-delta-ingest" / "src" / "main.rs").read_text()


def test_no_drop_todo_left():
    assert "TODO: Write invalid messages to dead-letter queue" not in MAIN_RS
    assert "For now, we just log and drop them" not in MAIN_RS


def test_default_dlq_topic_matches_config():
    cfg = yaml.safe_load((ROOT / "config.yml").read_text())
    expected = cfg["kafka"]["topics"]["dead_letter"]
    m = re.search(r'#\[arg\(long, default_value = "([^"]+)"\)\]\s*dlq_topic: String', MAIN_RS)
    assert m, "dlq_topic CLI option missing"
    assert m.group(1) == expected


def test_every_rejection_reason_is_dead_lettered_and_fails_closed():
    for reason in ("empty_payload", "parse_failed", "schema_validation_failed", "missing_required_field"):
        assert f'reason: "{reason}"' in MAIN_RS
    assert "dlq.send(" in MAIN_RS
    assert "Dead-letter produce failed; exiting without committing offsets" in MAIN_RS
    assert '.set("acks", "all")' in MAIN_RS
