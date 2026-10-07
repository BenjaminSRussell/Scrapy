"""#174: producers default to acks=all + idempotence; overridable for local use."""

from types import SimpleNamespace

import pytest

from src import pipelines as p
from src.utils.kafka_config import producer_durability_config


def test_default_is_acks_all_with_idempotence(monkeypatch):
    monkeypatch.delenv("KAFKA_PRODUCER_ACKS", raising=False)
    assert producer_durability_config() == {"acks": "all", "enable.idempotence": True}


@pytest.mark.parametrize("raw,expected", [("-1", "all"), ("ALL", "all"), ("1", "1"), ("0", "0")])
def test_env_override(monkeypatch, raw, expected):
    monkeypatch.setenv("KAFKA_PRODUCER_ACKS", raw)
    cfg = producer_durability_config()
    assert cfg["acks"] == expected
    assert cfg.get("enable.idempotence", False) is (expected == "all")


def test_invalid_acks_rejected():
    with pytest.raises(ValueError):
        producer_durability_config("2")


def _open(monkeypatch, producer_config=None):
    captured = {}

    class FakeProducer:
        def __init__(self, config):
            captured.update(config)

    monkeypatch.setattr(p, "Producer", FakeProducer, raising=False)
    k = p.KafkaPipeline("broker:9092", "items", producer_config=producer_config)
    k.open_spider(SimpleNamespace(name="scout"))
    return captured


def test_kafka_pipeline_producer_uses_acks_all(monkeypatch):
    monkeypatch.delenv("KAFKA_PRODUCER_ACKS", raising=False)
    cfg = _open(monkeypatch)
    assert cfg["acks"] == "all" and cfg["enable.idempotence"] is True
    assert cfg["max.in.flight.requests.per.connection"] <= 5  # idempotence requirement


def test_shipped_config_yml_keeps_acks_all(monkeypatch):
    # KAFKA_PRODUCER_CONFIG (config.yml kafka.producer) is applied on top of the
    # code defaults, so it must not quietly downgrade durability.
    from src import settings

    shipped = settings.KAFKA_PRODUCER_CONFIG or {}
    monkeypatch.delenv("KAFKA_PRODUCER_ACKS", raising=False)
    cfg = _open(monkeypatch, producer_config=shipped)
    assert str(cfg["acks"]) in {"all", "-1"}
    assert cfg["enable.idempotence"] is True
