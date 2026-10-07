"""#319: continuous Stage 2/3 workers take concurrency/batch size from config/env."""

import pytest

from src.core.config import Config, stage_worker_settings


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for name in ("STAGE2_CONCURRENT", "STAGE2_BATCH_SIZE", "STAGE3_CONCURRENT", "STAGE3_BATCH_SIZE"):
        monkeypatch.delenv(name, raising=False)


def _config(tmp_path, text):
    path = tmp_path / "config.yml"
    path.write_text(text)
    return Config(path)


def test_config_yml_values_are_used(tmp_path):
    cfg = _config(tmp_path, "stage2:\n  max_workers: 7\n  batch_size: 9\n")
    assert stage_worker_settings(2, 50, 100, config=cfg) == (7, 9)


def test_changing_config_changes_parallelism(tmp_path):
    a = _config(tmp_path, "stage3:\n  max_workers: 3\n  batch_size: 4\n")
    assert stage_worker_settings(3, 20, 50, config=a) == (3, 4)
    (tmp_path / "b").mkdir()
    b = _config(tmp_path / "b", "stage3:\n  max_workers: 11\n  batch_size: 12\n")
    assert stage_worker_settings(3, 20, 50, config=b) == (11, 12)


def test_env_overrides_config(tmp_path, monkeypatch):
    cfg = _config(tmp_path, "stage2:\n  max_workers: 7\n  batch_size: 9\n")
    monkeypatch.setenv("STAGE2_CONCURRENT", "13")
    monkeypatch.setenv("STAGE2_BATCH_SIZE", "17")
    assert stage_worker_settings(2, 50, 100, config=cfg) == (13, 17)


def test_invalid_values_fall_back_to_defaults(tmp_path, monkeypatch):
    cfg = _config(tmp_path, "stage2:\n  max_workers: zero\n  batch_size: -1\n")
    monkeypatch.setenv("STAGE2_CONCURRENT", "not-a-number")
    concurrent, batch = stage_worker_settings(2, 50, 100, config=cfg)
    # stages.stage2.concurrent default (50) from built-in defaults or the literal default
    assert concurrent == 50
    assert batch == 100


def test_repo_config_is_honoured():
    cfg = Config()  # project config.yml
    expected = (cfg.get("stage2.max_workers"), cfg.get("stage2.batch_size"))
    assert stage_worker_settings(2, 1, 1, config=cfg) == expected
