"""Feature-flag helper (#304): defaults, parsing, env > config precedence, call sites."""
from __future__ import annotations

import importlib

import pytest

from src.utils import feature_flags as ff


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes ", "on"])
def test_truthy_words(raw):
    assert ff.get_bool("X", False, env={"X": raw}, use_config=False) is True


@pytest.mark.parametrize("raw", ["0", "false", "No", "off"])
def test_falsy_words(raw):
    assert ff.get_bool("X", True, env={"X": raw}, use_config=False) is False


@pytest.mark.parametrize("default", [True, False])
def test_typo_and_empty_fall_back_to_default(default, caplog):
    assert ff.get_bool("X", default, env={"X": "ture"}, use_config=False) is default
    assert "Unrecognised" in caplog.text
    assert ff.get_bool("X", default, env={"X": ""}, use_config=False) is default


def test_unset_uses_default():
    assert ff.get_bool("NOPE", False, env={}, use_config=False) is False
    assert ff.get_bool("NOPE", True, env={}, use_config=False) is True
    assert ff.get_str("NOPE", "d", env={}, use_config=False) == "d"
    assert ff.get_int("NOPE", 7, env={}, use_config=False) == 7


def test_get_str_and_int():
    env = {"S": "  json ", "I": "42", "BAD": "4x"}
    assert ff.get_str("S", env=env, use_config=False) == "json"
    assert ff.get_int("I", env=env, use_config=False) == 42
    assert ff.get_int("BAD", 3, env=env, use_config=False) == 3


def test_config_section_used_when_env_unset(monkeypatch):
    monkeypatch.setattr(ff, "_config_value", lambda name: True if name == "MY_FLAG" else ff._UNSET)
    assert ff.get_bool("MY_FLAG", False, env={}) is True
    # env wins over config
    assert ff.get_bool("MY_FLAG", False, env={"MY_FLAG": "0"}) is False


def test_registry_experimental_defaults_off():
    assert ff.FLAGS["ENABLE_EXPERIMENTAL_SPIDERS"][0] is False
    assert ff.FLAGS["ASR_ENABLED"][0] is False
    snap = ff.snapshot(env={})
    assert snap["ENABLE_EXPERIMENTAL_SPIDERS"] is False
    assert snap["SSRF_GUARD_ENABLED"] is True and snap["KAFKA_DLQ_ENABLED"] is True


def _reload_settings():
    import src.settings as settings

    return importlib.reload(settings)


def test_settings_call_sites_use_flags(monkeypatch):
    monkeypatch.delenv("ENABLE_EXPERIMENTAL_SPIDERS", raising=False)
    monkeypatch.setenv("SSRF_GUARD_ENABLED", "off")  # previously only "0" disabled
    monkeypatch.setenv("ASR_ENABLED", "no")
    try:
        s = _reload_settings()
        assert s.ENABLE_EXPERIMENTAL_SPIDERS is False
        assert s.SSRF_GUARD_ENABLED is False
        assert s.ASR_ENABLED is False
        monkeypatch.setenv("ENABLE_EXPERIMENTAL_SPIDERS", "1")
        monkeypatch.setenv("SSRF_GUARD_ENABLED", "1")
        s = _reload_settings()
        assert s.ENABLE_EXPERIMENTAL_SPIDERS is True and s.SSRF_GUARD_ENABLED is True
    finally:
        monkeypatch.undo()
        _reload_settings()


@pytest.mark.parametrize("value, expect_calls", [("off", 0), ("1", 1)])
def test_kafka_dlq_kill_switch(monkeypatch, tmp_path, value, expect_calls):
    from src.pipelines import KafkaPipeline

    pipe = KafkaPipeline.__new__(KafkaPipeline)
    pipe.spill_dir = str(tmp_path / "spill")
    pipe.topic = "scraped_items"
    calls = []

    class _DLQ:
        def __getattr__(self, name):
            return lambda *a, **k: calls.append(name)

    pipe._dlq = _DLQ()
    monkeypatch.setenv("KAFKA_DLQ_ENABLED", value)
    pipe._dead_letter(b'{"url": "https://x"}', "produce_failed", "boom")
    assert (len(calls) > 0) == bool(expect_calls)
