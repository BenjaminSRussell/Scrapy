"""Large seed load preflight: estimate, Redis depth, confirm, sample (#453).

Fake config and depth only. No Redis server, no Delta lake.
"""

from __future__ import annotations

import pytest

from src.lakehouse import seed_preflight as sp

pytestmark = pytest.mark.unit


class FakeConfig:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def depth(monkeypatch):
    state = {"value": 0}
    monkeypatch.setattr(sp, "read_seed_queue_depth", lambda: state["value"])
    return state


def _no_prompt(prompt):
    raise AssertionError(f"unexpected prompt: {prompt}")


def test_preflight_settings_defaults_when_missing():
    assert sp.preflight_settings(FakeConfig({})) == {"warn_threshold": 10_000, "sample_size": 1_000}


def test_preflight_settings_reads_config_and_ignores_bad_values():
    config = FakeConfig({"seed_load.warn_threshold": "500", "seed_load.sample_size": "not-a-number"})
    assert sp.preflight_settings(config) == {"warn_threshold": 500, "sample_size": 1_000}
    assert sp.preflight_settings(FakeConfig({"seed_load.warn_threshold": 0}))["warn_threshold"] == 10_000


def test_at_or_below_threshold_loads_all_without_prompt(depth):
    config = FakeConfig({"seed_load.warn_threshold": 100})
    assert sp.confirm_seed_load(100, config=config, interactive=True, input_fn=_no_prompt) == 100


def test_above_threshold_assume_yes_loads_all(depth):
    config = FakeConfig({"seed_load.warn_threshold": 100})
    assert sp.confirm_seed_load(150, config=config, assume_yes=True, input_fn=_no_prompt) == 150


def test_above_threshold_non_interactive_refuses(depth):
    config = FakeConfig({"seed_load.warn_threshold": 100})
    assert sp.confirm_seed_load(150, config=config, interactive=False, input_fn=_no_prompt) is None


@pytest.mark.parametrize(("answer", "expected"), [("a", 150), ("all", 150), ("s", 40), ("sample", 40), ("q", None), ("", None)])
def test_above_threshold_interactive_answers(depth, answer, expected):
    config = FakeConfig({"seed_load.warn_threshold": 100, "seed_load.sample_size": 40})
    assert sp.confirm_seed_load(150, config=config, interactive=True, input_fn=lambda _p: answer) == expected


def test_explicit_sample_skips_prompt_and_caps_at_count(depth):
    config = FakeConfig({"seed_load.warn_threshold": 100})
    assert sp.confirm_seed_load(150, sample=25, config=config, interactive=False, input_fn=_no_prompt) == 25
    assert sp.confirm_seed_load(10, sample=25, config=config, interactive=False, input_fn=_no_prompt) == 10


def test_unknown_depth_still_gates_large_load(monkeypatch):
    monkeypatch.setattr(sp, "read_seed_queue_depth", lambda: None)
    config = FakeConfig({"seed_load.warn_threshold": 100})
    assert sp.confirm_seed_load(150, config=config, interactive=False, input_fn=_no_prompt) is None


def test_select_seeds_takes_random_subset_of_requested_size():
    records = [{"url": f"https://uconn.edu/{i}"} for i in range(50)]
    picked = sp.select_seeds(records, 10)
    assert len(picked) == 10
    assert all(r in records for r in picked)
    assert sp.select_seeds(records, None) is records
    assert sp.select_seeds(records, 50) is records
