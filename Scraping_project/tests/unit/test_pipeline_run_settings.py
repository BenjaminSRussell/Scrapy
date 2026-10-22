"""Pipeline run settings for the compose ``scraper`` entrypoint (src/main.py)."""

from __future__ import annotations

import asyncio
import time

import pytest

from src.core.config import PipelineRunSettings, pipeline_run_settings

_ENV_VARS = (
    "PIPELINE_MODE",
    "STAGE1_URL_LIMIT",
    "STAGE2_CONCURRENCY",
    "STAGE3_CONCURRENCY",
    "PIPELINE_LOOP_INTERVAL_SECONDS",
)


class _Cfg:
    def __init__(self, data: dict | None = None):
        self._data = data or {}

    def get(self, key, default=None):
        return self._data.get(key, default)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_match_previous_hard_coded_run():
    settings = pipeline_run_settings(_Cfg())

    assert settings == PipelineRunSettings(
        mode="once",
        stage1_url_limit=50,
        stage2_concurrent=10,
        stage3_concurrent=5,
        loop_interval_seconds=3600,
    )


def test_config_yml_values_are_used_when_env_unset():
    cfg = _Cfg(
        {
            "pipeline.mode": "loop",
            "pipeline.stage1_url_limit": 200,
            "pipeline.stage2_concurrency": 20,
            "pipeline.stage3_concurrency": 7,
            "pipeline.loop_interval_seconds": 60,
        }
    )

    settings = pipeline_run_settings(cfg)

    assert (settings.mode, settings.stage1_url_limit, settings.stage2_concurrent) == ("loop", 200, 20)
    assert (settings.stage3_concurrent, settings.loop_interval_seconds) == (7, 60)


def test_env_overrides_config(monkeypatch):
    monkeypatch.setenv("PIPELINE_MODE", "disabled")
    monkeypatch.setenv("STAGE1_URL_LIMIT", "5")
    monkeypatch.setenv("STAGE2_CONCURRENCY", "3")
    monkeypatch.setenv("STAGE3_CONCURRENCY", "2")
    monkeypatch.setenv("PIPELINE_LOOP_INTERVAL_SECONDS", "30")

    settings = pipeline_run_settings(_Cfg({"pipeline.mode": "once", "pipeline.stage1_url_limit": 999}))

    assert settings == PipelineRunSettings("disabled", 5, 3, 2, 30)


def test_empty_env_falls_through_to_config(monkeypatch):
    monkeypatch.setenv("PIPELINE_MODE", "")
    monkeypatch.setenv("STAGE1_URL_LIMIT", "")

    settings = pipeline_run_settings(_Cfg({"pipeline.stage1_url_limit": 12}))

    assert (settings.mode, settings.stage1_url_limit) == ("once", 12)


@pytest.mark.parametrize("bad", ["0", "-4", "many"])
def test_invalid_number_falls_through_to_default(monkeypatch, bad):
    monkeypatch.setenv("STAGE1_URL_LIMIT", bad)

    assert pipeline_run_settings(_Cfg()).stage1_url_limit == 50


def test_unknown_mode_fails_fast(monkeypatch):
    monkeypatch.setenv("PIPELINE_MODE", "forever")

    with pytest.raises(ValueError, match="PIPELINE_MODE"):
        pipeline_run_settings(_Cfg())


def test_orchestrator_main_passes_settings_to_run_full_pipeline(monkeypatch):
    from src.orchestrator import pipeline_orchestrator as po

    calls = {}

    class _FakeOrchestrator:
        async def run_full_pipeline(self, **kwargs):
            calls.update(kwargs)

    monkeypatch.setattr(po, "PipelineOrchestrator", _FakeOrchestrator)

    asyncio.run(po.main(PipelineRunSettings("once", 5, 3, 2, 60)))

    assert calls == {"stage1_url_limit": 5, "stage2_concurrent": 3, "stage3_concurrent": 2}


def test_entrypoint_once_runs_one_pass(monkeypatch):
    from src.orchestrator import pipeline_orchestrator as po
    from src.main import main as entry

    settings = PipelineRunSettings("once", 5, 3, 2, 60)
    seen = []

    async def _fake_main(received=None):
        seen.append(received)

    monkeypatch.setattr("src.core.config.pipeline_run_settings", lambda: settings)
    monkeypatch.setattr(po, "main", _fake_main)

    entry()

    assert seen == [settings]


def test_entrypoint_disabled_runs_nothing(monkeypatch):
    from src.orchestrator import pipeline_orchestrator as po
    from src.main import main as entry

    seen = []

    async def _fake_main(received=None):
        seen.append(received)

    monkeypatch.setattr("src.core.config.pipeline_run_settings", lambda: PipelineRunSettings("disabled", 5, 3, 2, 60))
    monkeypatch.setattr(po, "main", _fake_main)

    entry()

    assert seen == []


def test_entrypoint_loop_retries_failed_pass_and_sleeps_between_passes(monkeypatch):
    from src.orchestrator import pipeline_orchestrator as po
    from src.main import main as entry

    settings = PipelineRunSettings("loop", 5, 3, 2, 42)
    outcomes = [po.PipelineRunError("failed", {}), None]
    sleeps = []

    async def _fake_main(received=None):
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome

    def _fake_sleep(seconds):
        sleeps.append(seconds)
        if not outcomes:
            raise KeyboardInterrupt  # stop the endless loop after the scripted passes

    monkeypatch.setattr("src.core.config.pipeline_run_settings", lambda: settings)
    monkeypatch.setattr(po, "main", _fake_main)
    monkeypatch.setattr(time, "sleep", _fake_sleep)

    with pytest.raises(KeyboardInterrupt):
        entry()

    assert sleeps == [42, 42]
