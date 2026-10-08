"""#410: validation_failures is a declared, provisioned Kafka topic.

SchemaValidationPipeline published to ``validation_failures`` while config.yml
``kafka.topics`` listed only scraped_items and dead_letter, so the topic had no
partitions/retention contract and only existed if the broker auto-created it.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.settings import derive_scrapy_config
from src.utils import kafka_topics
from src.utils.kafka_topics import TopicSpec, ensure_topics, topic_plan

ROOT = Path(__file__).resolve().parents[2]
CONFIG = yaml.safe_load((ROOT / "config.yml").read_text())
HELM = ROOT / "k8s" / "helm" / "scraping-pipeline"


class FakeConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key, default=None):
        node = self.data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def get_section(self, name):
        return self.data.get(name)


def test_config_declares_every_required_topic_with_settings():
    topics = CONFIG["kafka"]["topics"]
    settings = CONFIG["kafka"]["topic_settings"]
    for logical in kafka_topics.REQUIRED_TOPICS:
        assert logical in topics, f"kafka.topics.{logical} missing from config.yml"
        assert settings[logical]["retention_ms"] > 0
    assert topics["validation_failures"] == "validation_failures"
    assert settings["validation_failures"]["partitions"] >= 1


def test_scrapy_setting_reads_the_config_key():
    cfg = FakeConfig({"kafka": {"topics": {"validation_failures": "vf-test"}}})
    assert derive_scrapy_config(cfg)["validation_failures_topic"] == "vf-test"
    # An explicit scrapy.* key still wins over the bridge.
    cfg = FakeConfig({"scrapy": {"validation_failures_topic": "explicit"},
                      "kafka": {"topics": {"validation_failures": "vf-test"}}})
    assert derive_scrapy_config(cfg)["validation_failures_topic"] == "explicit"


def test_live_settings_use_config_yml():
    from src import settings
    assert settings.VALIDATION_FAILURES_TOPIC == CONFIG["kafka"]["topics"]["validation_failures"]


def test_pipeline_default_matches_the_contract():
    import inspect

    from src.pipelines import SchemaValidationPipeline
    default = inspect.signature(SchemaValidationPipeline.__init__).parameters["validation_failures_topic"].default
    assert default == kafka_topics.DEFAULT_TOPIC_NAMES["validation_failures"]


def test_topic_plan_applies_settings():
    plan = {s.logical: s for s in topic_plan(FakeConfig(CONFIG))}
    vf = plan["validation_failures"]
    assert vf.name == "validation_failures"
    assert vf.partitions == 1
    assert vf.config == {"retention.ms": str(CONFIG["kafka"]["topic_settings"]["validation_failures"]["retention_ms"])}
    assert plan["scraped_items"].partitions == -1  # broker default


def test_topic_plan_fills_defaults_when_config_is_bare():
    plan = {s.logical: s.name for s in topic_plan(FakeConfig({}))}
    assert plan == kafka_topics.DEFAULT_TOPIC_NAMES


class FakeFuture:
    def __init__(self, exc=None):
        self.exc = exc

    def result(self):
        if self.exc:
            raise self.exc


class FakeAdmin:
    def __init__(self, existing, errors=None):
        self.existing = existing
        self.errors = errors or {}
        self.created = []

    def list_topics(self, timeout):
        return SimpleNamespace(topics={t: None for t in self.existing})

    def create_topics(self, new_topics, operation_timeout):
        self.created.extend(new_topics)
        return {t.topic: FakeFuture(self.errors.get(t.topic)) for t in new_topics}


SPECS = [TopicSpec("scraped_items", "scraped-items"),
         TopicSpec("validation_failures", "validation_failures", 1, -1, {"retention.ms": "1209600000"}),
         TopicSpec("dead_letter", "scraped-items-dlq", 1)]


def test_ensure_creates_only_missing_topics():
    pytest.importorskip("confluent_kafka")
    admin = FakeAdmin(existing={"scraped-items"})
    result = ensure_topics(admin, SPECS)
    assert result == {"scraped-items": "exists", "validation_failures": "created",
                      "scraped-items-dlq": "created"}
    vf = next(t for t in admin.created if t.topic == "validation_failures")
    assert vf.num_partitions == 1
    assert vf.config == {"retention.ms": "1209600000"}


def test_ensure_is_a_noop_when_everything_exists():
    admin = FakeAdmin(existing={s.name for s in SPECS})
    assert set(ensure_topics(admin, SPECS).values()) == {"exists"}
    assert admin.created == []


def test_ensure_tolerates_a_create_race_and_reports_errors():
    pytest.importorskip("confluent_kafka")
    admin = FakeAdmin(existing=set(), errors={
        "scraped-items": Exception("KafkaError{code=TOPIC_ALREADY_EXISTS}"),
        "scraped-items-dlq": Exception("KafkaError{code=POLICY_VIOLATION}"),
    })
    result = ensure_topics(admin, SPECS)
    assert result["scraped-items"] == "exists"
    assert result["validation_failures"] == "created"
    assert result["scraped-items-dlq"].startswith("error")


def test_cli_exit_code_reflects_errors(monkeypatch):
    pytest.importorskip("confluent_kafka")
    import confluent_kafka.admin as ck_admin
    admin = FakeAdmin(existing=set(), errors={"validation_failures": Exception("boom")})
    monkeypatch.setattr(ck_admin, "AdminClient", lambda conf: admin)
    assert kafka_topics.main([]) == 1
    admin.errors.clear()
    admin.existing = set()
    assert kafka_topics.main([]) == 0


def test_cli_dry_run_does_not_connect(monkeypatch):
    pytest.importorskip("confluent_kafka")
    import confluent_kafka.admin as ck_admin

    def boom(conf):
        raise AssertionError("dry run must not connect")
    monkeypatch.setattr(ck_admin, "AdminClient", boom)
    assert kafka_topics.main(["--dry-run"]) == 0


def test_admin_config_uses_producer_env(monkeypatch):
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "k:9092")
    monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
    monkeypatch.delenv("KAFKA_SASL_USERNAME", raising=False)
    conf = kafka_topics.admin_config_from_env()
    assert conf == {"bootstrap.servers": "k:9092", "security.protocol": "SASL_SSL"}


def test_helm_hook_job_and_network_access():
    job = (HELM / "templates" / "kafka-topics-job.yaml").read_text()
    assert '"helm.sh/hook": post-install,post-upgrade' in job
    assert 'command: ["python", "-m", "src.utils.kafka_topics"]' in job
    assert "-app-env" in job  # KAFKA_BOOTSTRAP_SERVERS
    values = yaml.safe_load((HELM / "values.yaml").read_text())
    assert values["kafka"]["topicsJob"]["enabled"] is True
    netpol = (HELM / "templates" / "networkpolicy.yaml").read_text()
    core_egress = netpol.split("-core-egress")[1].split("---")[0]
    kafka_ingress = netpol.split("-kafka-ingress")[1].split("---")[0]
    assert "kafka-topics" in core_egress and "kafka-topics" in kafka_ingress
