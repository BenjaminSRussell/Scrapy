"""#788: CONFIG_ENV=<name> deep-merges config/<name>.yml over config.yml."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from src.core.config import Config, config_env_name, deep_merge, overlay_path_for

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch):
    monkeypatch.delenv("CONFIG_ENV", raising=False)


def _write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else yaml.safe_dump(data))
    return path


BASE = {
    "redis": {"host": "localhost", "port": 6379, "db": 0},
    "logging": {"level": "INFO", "file": "./data/logs/pipeline.log"},
    "stage2": {"max_workers": 100, "batch_size": 50, "tags": ["a", "b", "c"]},
}


@pytest.fixture
def base_cfg(tmp_path) -> Path:
    return _write(tmp_path / "config.yml", BASE)


# --- deep_merge ---------------------------------------------------------------


def test_deep_merge_nested_maps_and_replacing_leaves():
    merged = deep_merge(BASE, {"redis": {"host": "redis"}, "stage2": {"tags": ["x"]}, "new": {"k": 1}})
    assert merged["redis"] == {"host": "redis", "port": 6379, "db": 0}
    assert merged["stage2"]["tags"] == ["x"]          # lists replace, not append
    assert merged["stage2"]["max_workers"] == 100      # untouched siblings kept
    assert merged["new"] == {"k": 1}
    assert merged["logging"] == BASE["logging"]


def test_deep_merge_null_and_type_changes_replace():
    merged = deep_merge(BASE, {"logging": {"file": None}, "redis": "redis://x"})
    assert merged["logging"] == {"level": "INFO", "file": None}
    assert merged["redis"] == "redis://x"
    assert deep_merge({"a": 1}, {"a": {"b": 2}}) == {"a": {"b": 2}}


def test_deep_merge_never_mutates_inputs():
    base = {"a": {"b": [1, 2]}}
    overlay = {"a": {"c": {"d": 1}}}
    merged = deep_merge(base, overlay)
    merged["a"]["b"].append(3)
    merged["a"]["c"]["d"] = 99
    assert base == {"a": {"b": [1, 2]}}
    assert overlay == {"a": {"c": {"d": 1}}}


# --- env name -----------------------------------------------------------------


@pytest.mark.parametrize("raw, expected", [("prod", "prod"), (" staging ", "staging"), ("dev_2", "dev_2"), ("eu-west", "eu-west"), ("", None), ("   ", None)])
def test_config_env_name_valid(raw, expected):
    assert config_env_name(raw) == expected


@pytest.mark.parametrize("raw", ["../secrets", "a/b", "/etc/passwd", "prod.yml", "-x", "pro d", "prod\n../x", "ü"])
def test_config_env_name_rejects_path_tricks(raw):
    with pytest.raises(ValueError):
        config_env_name(raw)


def test_overlay_path_is_next_to_base(tmp_path):
    assert overlay_path_for(tmp_path / "config.yml", "prod") == tmp_path / "config" / "prod.yml"
    assert overlay_path_for(tmp_path / "config.yml", None) is None


# --- Config loading -----------------------------------------------------------


def test_overlay_merged_when_config_env_set(base_cfg, monkeypatch):
    overlay = _write(base_cfg.parent / "config" / "prod.yml", {"redis": {"host": "redis"}, "logging": {"level": "WARNING"}})
    monkeypatch.setenv("CONFIG_ENV", "prod")
    cfg = Config(base_cfg)
    assert cfg.get("redis.host") == "redis"
    assert cfg.get("redis.port") == 6379
    assert cfg.get("logging.level") == "WARNING"
    assert cfg.get("logging.file") == "./data/logs/pipeline.log"
    assert cfg.active_overlay == overlay


def test_explicit_config_env_argument(base_cfg):
    _write(base_cfg.parent / "config" / "staging.yml", {"stage2": {"max_workers": 8}})
    cfg = Config(base_cfg, config_env="staging")
    assert cfg.get("stage2.max_workers") == 8
    assert cfg.get("stage2.batch_size") == 50


def test_missing_overlay_means_base_only(base_cfg, monkeypatch, caplog):
    monkeypatch.setenv("CONFIG_ENV", "qa")
    with caplog.at_level(logging.WARNING, logger="src.core.config"):
        cfg = Config(base_cfg)
    assert cfg.get_raw_config() == BASE
    assert cfg.active_overlay is None
    assert "does not exist; using base config only" in caplog.text


def test_unset_env_means_base_only_even_if_overlays_exist(base_cfg):
    _write(base_cfg.parent / "config" / "prod.yml", {"redis": {"host": "redis"}})
    cfg = Config(base_cfg)
    assert cfg.get("redis.host") == "localhost"
    assert cfg.active_overlay is None


def test_empty_overlay_is_a_noop(base_cfg):
    _write(base_cfg.parent / "config" / "dev.yml", "# nothing yet\n")
    cfg = Config(base_cfg, config_env="dev")
    assert cfg.get_raw_config() == BASE


def test_reload_picks_up_overlay_edits(base_cfg):
    overlay = _write(base_cfg.parent / "config" / "prod.yml", {"stage2": {"max_workers": 10}})
    cfg = Config(base_cfg, config_env="prod")
    gen = cfg.generation
    _write(overlay, {"stage2": {"max_workers": 20}})
    assert cfg.reload() is True
    assert cfg.get("stage2.max_workers") == 20
    assert cfg.generation == gen + 1


def test_broken_overlay_on_reload_keeps_previous_snapshot(base_cfg):
    overlay = _write(base_cfg.parent / "config" / "prod.yml", {"stage2": {"max_workers": 10}})
    cfg = Config(base_cfg, config_env="prod")
    gen = cfg.generation
    _write(overlay, "stage2: [unterminated\n")
    assert cfg.reload() is False
    assert cfg.get("stage2.max_workers") == 10
    assert cfg.generation == gen
    _write(overlay, ["not", "a", "mapping"])
    assert cfg.reload() is False
    assert cfg.get("stage2.max_workers") == 10


def test_invalid_env_name_on_reload_keeps_previous_snapshot(base_cfg, monkeypatch):
    cfg = Config(base_cfg)
    monkeypatch.setenv("CONFIG_ENV", "../../etc")
    assert cfg.reload() is False
    assert cfg.get_raw_config() == BASE


def test_broken_overlay_on_first_load_behaves_like_broken_base(base_cfg):
    # parity with an unparseable config.yml: first load falls back to defaults
    _write(base_cfg.parent / "config" / "prod.yml", "redis: [\n")
    cfg = Config(base_cfg, config_env="prod")
    assert cfg.active_overlay is None
    assert cfg.get("redis.host") is not None
    assert cfg.get("stage2.tags") is None  # base not used


# --- shipped examples ---------------------------------------------------------


def _paths(d: dict, prefix=()):
    for k, v in d.items():
        if isinstance(v, dict):
            yield from _paths(v, prefix + (k,))
        else:
            yield prefix + (k,)


@pytest.mark.parametrize("name", ["dev", "prod"])
def test_examples_only_override_keys_that_exist(name):
    base = yaml.safe_load((ROOT / "config.yml").read_text())
    example = yaml.safe_load((ROOT / "config" / f"{name}.yml.example").read_text())
    assert isinstance(example, dict) and example
    for path in _paths(example):
        node = base
        for part in path:
            assert isinstance(node, dict) and part in node, f"{name}.yml.example overrides unknown key {'.'.join(path)}"
            node = node[part]


def test_dev_example_merges_over_real_config(tmp_path):
    base = _write(tmp_path / "config.yml", (ROOT / "config.yml").read_text())
    _write(tmp_path / "config" / "dev.yml", (ROOT / "config" / "dev.yml.example").read_text())
    cfg = Config(base, config_env="dev")
    real = yaml.safe_load((ROOT / "config.yml").read_text())
    assert cfg.get("logging.level") == "DEBUG"
    assert cfg.get("stage2.max_workers") == 4
    assert cfg.get("stage2.batch_size") == real["stage2"]["batch_size"]
    assert cfg.get("redis") == real["redis"]


def test_example_files_are_not_loaded_as_overlays():
    # config/<name>.yml is the overlay; *.yml.example never matches
    assert overlay_path_for(ROOT / "config.yml", "dev").name == "dev.yml"
