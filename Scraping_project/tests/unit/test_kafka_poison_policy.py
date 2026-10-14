"""#177: a malformed ingest payload is dead-lettered and its offset is committed.

No broker. The policy is a pure function. A fixture of bad bytes is driven
through it to show the partition still reaches the next payload.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_policy():
    """Load the policy file directly so the test does not import src.utils."""
    path = ROOT / "src" / "utils" / "kafka_poison_policy.py"
    spec = importlib.util.spec_from_file_location("kafka_poison_policy", path)
    module = importlib.util.module_from_spec(spec)
    # dataclass looks the class up in sys.modules while the body executes.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_policy = _load_policy()
DLQ_AND_COMMIT = _policy.DLQ_AND_COMMIT
RETRY = _policy.RETRY
decide = _policy.decide
poison_action = _policy.poison_action

MAIN_RS = ROOT / "kafka-delta-ingest" / "src" / "main.rs"
PIPELINES = ROOT / "src" / "pipelines.py"
VALUES = ROOT / "k8s" / "helm" / "scraping-pipeline" / "values.yaml"
DEPLOY = ROOT / "k8s" / "helm" / "scraping-pipeline" / "templates" / "kafka-delta-ingestor-deployment.yaml"
RULES = ROOT / "monitoring" / "alerting_rules.yml"
HELM_RULES = ROOT / "k8s" / "helm" / "scraping-pipeline" / "files" / "monitoring" / "alerting_rules.yml"
MAPPING = ROOT / "k8s" / "helm" / "scraping-pipeline" / "files" / "monitoring" / "statsd_mapping.yml"
DOC = ROOT / "docs" / "kafka_poison_message_policy.md"

# Same bytes the Rust classifier rejects as parse_failed.
MALFORMED = b"{not json"
GOOD = (
    b'{"url":"https://example.edu/a","scraped_at_utc":"2026-10-07T22:30:00Z",'
    b'"spider_name":"discovery"}'
)

# The three published SLO expressions. This change must leave them byte-for-byte.
SLO_SNIPPETS = (
    "expr: max_over_time(kafka_consumergroup_lag_max[5m]) > 250000",
    "expr: max by (queue, site_id) (redis_queue_length) > 10000",
    "\n".join(
        [
            "        expr: |",
            "          (",
            "            stage2:success_ratio:24h < 0.99",
            "          )",
            "          and",
            "          (",
            "            sum(increase(stage2_rows_total[24h])) > 0",
            "          )",
        ]
    ),
)


def _deserializes(payload: bytes) -> bool:
    try:
        json.loads(payload)
    except json.JSONDecodeError:
        return False
    return True


def _drive(payloads: list[bytes], max_failures: int) -> tuple[list[int], list[bytes]]:
    """In-order consume. Retries re-read the same index; a DLQ decision commits it."""
    committed: list[int] = []
    dlq: list[bytes] = []
    attempts: dict[int, int] = {}
    index = 0
    steps = 0
    bound = len(payloads) * (max(1, max_failures) + 1) + 5
    while index < len(payloads):
        steps += 1
        if steps > bound:
            raise AssertionError("partition did not advance past a payload")
        raw = payloads[index]
        if _deserializes(raw):
            attempts.pop(index, None)
            committed.append(index)
            index += 1
            continue
        attempts[index] = attempts.get(index, 0) + 1
        decision = decide(attempts[index], max_failures)
        if decision.action == RETRY:
            assert decision.commit_offset is False and decision.send_to_dlq is False
            continue
        assert decision.action == DLQ_AND_COMMIT
        assert decision.commit_offset is True and decision.send_to_dlq is True
        dlq.append(raw)
        committed.append(index)
        index += 1
    return committed, dlq


def test_retries_until_the_limit_then_dead_letters_and_commits():
    assert poison_action(1, 3) == RETRY
    assert poison_action(2, 3) == RETRY
    assert [poison_action(n, 3) for n in (3, 4)] == [DLQ_AND_COMMIT, DLQ_AND_COMMIT]
    assert decide(2, 3).commit_offset is False
    assert decide(3, 3) == decide(3, 3)
    committed = decide(3, 3)
    assert committed.commit_offset and committed.send_to_dlq


def test_zero_limit_still_skips_on_the_first_failure():
    decision = decide(1, 0)
    assert decision.action == DLQ_AND_COMMIT
    assert decision.commit_offset is True


def test_malformed_fixture_does_not_block_the_following_payload():
    assert _deserializes(MALFORMED) is False
    assert _deserializes(GOOD) is True
    payloads = [MALFORMED, GOOD, MALFORMED, GOOD]
    committed, dlq = _drive(payloads, max_failures=3)
    assert committed == [0, 1, 2, 3]
    assert dlq == [MALFORMED, MALFORMED]
    # Two retries, then the third attempt commits. The good payload is not stuck.
    assert poison_action(1, 3) == RETRY
    assert poison_action(3, 3) == DLQ_AND_COMMIT


def test_consumer_entrypoint_wires_the_same_rule():
    src = MAIN_RS.read_text()
    assert "async fn ingest(" in src
    assert "fn poison_action(failures: u32, max_failures: u32)" in src
    assert "PoisonAction::DlqAndCommit" in src
    assert "PoisonAction::Retry" in src
    assert 'metrics.incr("messages.dead_lettered")' in src
    assert "Dead-letter produce failed; exiting without committing offsets" in src
    assert "default_value_t = 3" in src
    assert "max_deserialize_failures" in src
    # Empty-buffer poison path commits without waiting for another record.
    assert "should_write && buffer.is_empty()" in src
    assert "pending_offsets.commit(" in src
    doc = DOC.read_text()
    assert "async fn ingest" in doc
    assert "SchemaValidationPipeline" in doc
    assert "kafka-delta-ingestor-deployment.yaml" in doc
    # Producer path cites the consumer. The #219 counter stays.
    pipelines = PIPELINES.read_text()
    assert 'PIPELINE_ERRORS = _PErrCounter(' in pipelines
    assert '"pipeline_errors_total"' in pipelines
    gate = pipelines.split("class SchemaValidationPipeline:", 1)[1][:600]
    assert "async fn ingest" in gate
    assert "validation_failures" in gate


def test_helm_values_and_alert_match_the_policy():
    values = VALUES.read_text()
    assert 'dlqTopic: "scraped-items-dlq"' in values
    assert "maxDeserializeFailures: 3" in values
    deploy = DEPLOY.read_text()
    assert "--dlq-topic" in deploy
    assert "--max-deserialize-failures" in deploy
    assert "kafkaDeltaIngestor.config.dlqTopic" in deploy
    assert "kafkaDeltaIngestor.config.maxDeserializeFailures" in deploy
    mapping = MAPPING.read_text()
    assert 'match: "kafka_delta_ingest.messages.dead_lettered"' in mapping
    assert 'name: "kafka_delta_ingest_messages_dead_lettered_total"' in mapping
    rules = RULES.read_text()
    assert rules == HELM_RULES.read_text()
    assert "alert: KafkaPoisonMessageRate" in rules
    assert "sum(rate(kafka_delta_ingest_messages_dead_lettered_total[5m])) > 0.1" in rules
    # Not a fourth SLO, and the three existing expressions are untouched.
    assert "KafkaPoisonMessageRate" not in rules.split("- name: pipeline_slos", 1)[1]
    for snippet in SLO_SNIPPETS:
        assert snippet in rules
    stage2 = rules.split("alert: Stage2SuccessSLO", 1)[1].split("for: 15m", 1)[0]
    assert "bool" not in stage2
