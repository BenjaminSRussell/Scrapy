"""#285: Kafka messages are keyed by url_hash. #464: idempotent producer can be enforced."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import pipelines as p
from src.lakehouse.seed_manager import default_url_hasher
from src.utils.kafka_config import (
    enforce_idempotent_producer,
    idempotence_required,
    idempotence_violations,
    message_key,
)

ROOT = Path(__file__).resolve().parents[2]
SPIDER = SimpleNamespace(name="scout")
URL = "https://www.uconn.edu/admissions/"


# ----------------------------------------------------------------- #285 key
def test_key_is_url_hash_when_present():
    assert message_key({"url": URL, "url_hash": "abc123"}) == b"abc123"


def test_missing_url_hash_is_derived_with_the_lake_hasher():
    assert message_key({"url": URL}) == default_url_hasher(URL).encode()
    assert message_key({"url": URL, "url_hash": "  "}) == default_url_hasher(URL).encode()


def test_identical_urls_get_identical_key_bytes():
    a = message_key({"url": URL, "title": "v1"})
    b = message_key({"url": URL, "title": "v2", "content": "changed"})
    c = message_key({"url": URL + "other"})
    assert a == b and a != c


def test_other_key_field_and_fallback_to_url():
    assert message_key({"url": URL, "domain": "uconn.edu"}, "domain") == b"uconn.edu"
    assert message_key({"url": URL}, "domain") == URL.encode()
    assert message_key({"url": URL, "depth": 3}, "depth") == b"3"


@pytest.mark.parametrize("field", ["", None])
def test_empty_field_disables_keying(field):
    assert message_key({"url": URL, "url_hash": "x"}, field) is None


def test_no_url_and_no_field_is_unkeyed():
    assert message_key({"title": "orphan"}) is None


class KeyedProducer:
    def __init__(self, config=None):
        self.config = config or {}
        self.sent = []

    def produce(self, topic, value, callback, key=None):
        self.sent.append((topic, key, json.loads(value)))

    def poll(self, timeout=0):
        return 0

    def flush(self, timeout=None):
        return 0


def _pipeline(tmp_path, **kw):
    k = p.KafkaPipeline("broker:9092", "items", spill_dir=tmp_path, **kw)
    k.producer = KeyedProducer()
    return k


def test_pipeline_produces_with_url_hash_key(tmp_path):
    k = _pipeline(tmp_path)
    k.process_item({"url": URL, "url_hash": "h1", "spider_name": "scout"}, SPIDER)
    k.process_item({"url": URL, "spider_name": "scout"}, SPIDER)
    keys = [key for _, key, _ in k.producer.sent]
    assert keys == [b"h1", default_url_hasher(URL).encode()]


def test_pipeline_key_field_disabled_sends_unkeyed(tmp_path):
    k = _pipeline(tmp_path, message_key_field="")
    k.process_item({"url": URL, "url_hash": "h1"}, SPIDER)
    assert k.producer.sent[0][1] is None


def test_settings_default_key_field_is_url_hash():
    from src import settings

    assert settings.KAFKA_MESSAGE_KEY_FIELD == "url_hash"


# --------------------------------------------------------- #464 enforcement
GOOD = {"acks": "all", "enable.idempotence": True, "max.in.flight.requests.per.connection": 5}


@pytest.mark.parametrize(
    "override,needle",
    [
        ({"acks": "1"}, "acks"),
        ({"enable.idempotence": False}, "enable.idempotence"),
        ({"max.in.flight.requests.per.connection": 10}, "max.in.flight"),
    ],
)
def test_violations_are_reported(override, needle):
    cfg = {**GOOD, **override}
    problems = idempotence_violations(cfg)
    assert len(problems) == 1 and needle in problems[0]
    with pytest.raises(ValueError, match=needle):
        enforce_idempotent_producer(cfg)


def test_good_config_passes():
    assert idempotence_violations(GOOD) == []
    assert idempotence_violations({**GOOD, "acks": -1, "enable.idempotence": "true"}) == []


@pytest.mark.parametrize("raw,expected", [("true", True), ("1", True), ("ON", True), ("false", False), ("", False)])
def test_idempotence_required_env(monkeypatch, raw, expected):
    monkeypatch.setenv("KAFKA_REQUIRE_IDEMPOTENCE", raw)
    assert idempotence_required() is expected


def _open(monkeypatch, **kw):
    captured = {}

    class FakeProducer:
        def __init__(self, config):
            captured.update(config)

    monkeypatch.setattr(p, "Producer", FakeProducer, raising=False)
    k = p.KafkaPipeline("broker:9092", "items", **kw)
    k.open_spider(SPIDER)
    return captured


def test_enforced_pipeline_starts_with_shipped_config(monkeypatch):
    from src import settings

    monkeypatch.delenv("KAFKA_PRODUCER_ACKS", raising=False)
    cfg = _open(monkeypatch, producer_config=settings.KAFKA_PRODUCER_CONFIG, require_idempotence=True)
    assert idempotence_violations(cfg) == []


@pytest.mark.parametrize("override", [{"acks": "1"}, {"enable.idempotence": False},
                                      {"max.in.flight.requests.per.connection": 20}])
def test_enforced_pipeline_refuses_non_idempotent_override(monkeypatch, override):
    monkeypatch.delenv("KAFKA_PRODUCER_ACKS", raising=False)
    with pytest.raises(ValueError, match="not idempotent"):
        _open(monkeypatch, producer_config=override, require_idempotence=True)


def test_env_flag_enforces_when_not_passed(monkeypatch):
    monkeypatch.setenv("KAFKA_REQUIRE_IDEMPOTENCE", "true")
    monkeypatch.setenv("KAFKA_PRODUCER_ACKS", "1")  # local downgrade is refused in enforced envs
    with pytest.raises(ValueError):
        _open(monkeypatch)


def test_unenforced_local_downgrade_still_allowed(monkeypatch):
    monkeypatch.delenv("KAFKA_REQUIRE_IDEMPOTENCE", raising=False)
    monkeypatch.setenv("KAFKA_PRODUCER_ACKS", "1")
    cfg = _open(monkeypatch)
    assert cfg["acks"] == "1"  # #174 local override keeps working


def test_helm_configmap_enforces_idempotence_and_key():
    cm = (ROOT / "k8s/helm/scraping-pipeline/templates/application-configmap.yaml").read_text()
    assert 'KAFKA_REQUIRE_IDEMPOTENCE: "true"' in cm
    assert 'KAFKA_MESSAGE_KEY_FIELD: "url_hash"' in cm


def test_config_yml_key_options_are_not_passed_to_librdkafka():
    from src import settings

    assert "message_key_field" not in settings.KAFKA_PRODUCER_CONFIG
    assert "require_idempotence" not in settings.KAFKA_PRODUCER_CONFIG
