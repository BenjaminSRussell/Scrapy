"""Experimental spider gate (#391 / #442): import ok, crawl refused without opt-in."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from scrapy.settings import Settings

from src.stage1.experimental import gate

ROOT = Path(__file__).resolve().parents[3]


class _Crawler:
    def __init__(self, **settings):
        self.settings = Settings(settings)


def _lab_spiders():
    from src.stage1.experimental.deep_dive_spider import DeepDiveSpider
    from src.stage1.experimental.depth_spider import DepthSpider
    from src.stage1.experimental.js_spider import JavaScriptSpider

    return [JavaScriptSpider, DeepDiveSpider, DepthSpider]


def test_lab_spider_names_match_registry():
    assert sorted(cls.name for cls in _lab_spiders()) == sorted(gate.EXPERIMENTAL_SPIDERS)
    assert "scout" in gate.SUPPORTED_SPIDERS and "scout" not in gate.EXPERIMENTAL_SPIDERS


@pytest.mark.parametrize("idx", [0, 1, 2])
def test_from_crawler_refuses_without_opt_in(monkeypatch, idx):
    monkeypatch.delenv(gate.FLAG, raising=False)
    cls = _lab_spiders()[idx]
    assert issubclass(cls, gate.ExperimentalSpiderMixin)
    with pytest.raises(gate.ExperimentalSpiderDisabled, match=cls.name):
        cls.from_crawler(_Crawler())


def test_scout_is_not_gated():
    from src.stage1.scout_spider import ScoutSpider

    assert not issubclass(ScoutSpider, gate.ExperimentalSpiderMixin)


@pytest.mark.parametrize(
    "settings, env, expected",
    [
        ({}, {}, False),
        ({gate.FLAG: True}, {}, True),
        ({gate.FLAG: "1"}, {}, True),
        ({}, {gate.FLAG: "yes"}, True),
        ({gate.FLAG: "0"}, {}, False),
    ],
)
def test_experimental_enabled_sources(settings, env, expected):
    assert gate.experimental_enabled(Settings(settings), env=env) is expected


def test_require_experimental_logs_warning_when_enabled(caplog):
    gate.require_experimental("javascript", Settings({gate.FLAG: True}))
    assert "EXPERIMENTAL" in caplog.text and "Playwright" in caplog.text


def test_prerequisites_documented():
    readme = (ROOT / "src/stage1/experimental/README.md").read_text()
    for name in gate.EXPERIMENTAL_SPIDERS:
        assert f"`{name}`" in readme
    assert "12 GB" in readme and "playwright install chromium" in readme


def _cli(*args, env_extra=None):
    import os

    env = {k: v for k, v in os.environ.items() if k != gate.FLAG}
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "cli.py", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60
    )


def test_cli_deep_dive_refuses_without_flag():
    r = _cli("deep_dive")
    assert r.returncode == 2
    assert "EXPERIMENTAL" in r.stderr and "--experimental" in r.stderr


def test_cli_scrapy_refuses_lab_spider_without_flag():
    r = _cli("scrapy", "--spiders", "javascript")
    assert r.returncode == 2
    assert "javascript" in r.stderr


def test_cli_gate_helper_opt_in(monkeypatch):
    import cli

    monkeypatch.delenv(gate.FLAG, raising=False)
    assert cli._gate_experimental_spiders(["scout"], False) is True
    assert cli._gate_experimental_spiders(["deep_dive"], False) is False
    assert cli._gate_experimental_spiders(["deep_dive"], True) is True
    import os

    assert os.environ[gate.FLAG] == "1"
