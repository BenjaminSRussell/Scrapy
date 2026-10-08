"""#329: Stage 2 quality gates come from config.yml / env, defaults unchanged."""

import asyncio
import logging

import pytest

import src.stage2.stage2_worker as sw
from src.core.config import DEFAULT_STAGE2_THRESHOLDS, Config, Stage2Thresholds, stage2_quality_thresholds

ENV = ("STAGE2_MIN_WORD_COUNT", "STAGE2_MIN_TEXT_TO_HTML_RATIO", "STAGE2_MASSIVE_DOC_THRESHOLD")


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def _config(tmp_path, text):
    path = tmp_path / "config.yml"
    path.write_text(text)
    return Config(path)


def test_defaults_preserve_todays_values(tmp_path):
    assert stage2_quality_thresholds(_config(tmp_path, "other: 1\n")) == Stage2Thresholds(50, 0.1, 50000)
    assert DEFAULT_STAGE2_THRESHOLDS == Stage2Thresholds(50, 0.1, 50000)


def test_shipped_config_yml_matches_defaults():
    assert stage2_quality_thresholds(Config()) == DEFAULT_STAGE2_THRESHOLDS


def test_config_values_are_used(tmp_path):
    cfg = _config(tmp_path, "stage2:\n  min_word_count: 120\n  min_text_to_html_ratio: 0.25\n  massive_doc_threshold: 9000\n")
    assert stage2_quality_thresholds(cfg) == Stage2Thresholds(120, 0.25, 9000)


def test_legacy_stages_section_is_honoured(tmp_path):
    cfg = _config(tmp_path, "stages:\n  stage2:\n    min_word_count: 10\n")
    assert stage2_quality_thresholds(cfg).min_word_count == 10


def test_env_beats_config(tmp_path, monkeypatch):
    cfg = _config(tmp_path, "stage2:\n  min_word_count: 120\n  massive_doc_threshold: 9000\n")
    monkeypatch.setenv("STAGE2_MIN_WORD_COUNT", "5")
    monkeypatch.setenv("STAGE2_MIN_TEXT_TO_HTML_RATIO", "0.02")
    t = stage2_quality_thresholds(cfg)
    assert (t.min_word_count, t.min_text_to_html_ratio, t.massive_doc_threshold) == (5, 0.02, 9000)


@pytest.mark.parametrize(
    "yaml_text",
    [
        "stage2:\n  min_word_count: -3\n  min_text_to_html_ratio: 1.5\n  massive_doc_threshold: 0\n",
        "stage2:\n  min_word_count: lots\n  min_text_to_html_ratio: high\n  massive_doc_threshold: big\n",
    ],
)
def test_invalid_values_fall_back(tmp_path, yaml_text):
    assert stage2_quality_thresholds(_config(tmp_path, yaml_text)) == DEFAULT_STAGE2_THRESHOLDS


def test_zero_word_count_is_allowed(tmp_path):
    assert stage2_quality_thresholds(_config(tmp_path, "stage2:\n  min_word_count: 0\n")).min_word_count == 0


# ------------------------------------------------- behaviour in the workers
def _html(words: int) -> str:
    return "<html><head><title>T</title></head><body><p>" + "word " * words + "</p></body></html>"


def test_changing_config_changes_stage2_classification(monkeypatch, caplog):
    page = _html(80)
    with caplog.at_level(logging.INFO, logger="src.stage2.stage2_worker"):
        default = sw.Stage2Worker(max_concurrent=1)
    assert any("Quality thresholds: min_word_count=50" in r.getMessage() for r in caplog.records)
    rec = asyncio.run(default._analyze_html("https://a.edu/x", "h", page, False))
    assert rec["is_low_quality"] is False

    monkeypatch.setenv("STAGE2_MIN_WORD_COUNT", "100")
    strict = sw.Stage2Worker(max_concurrent=1)
    assert strict.MIN_WORD_COUNT == 100
    rec = asyncio.run(strict._analyze_html("https://a.edu/x", "h", page, False))
    assert rec["is_low_quality"] is True  # same page, stricter config, no code edit


def test_massive_threshold_from_env_routes_to_stage4(monkeypatch):
    monkeypatch.setenv("STAGE2_MASSIVE_DOC_THRESHOLD", "100")
    w = sw.Stage2Worker(max_concurrent=1)
    routed = []

    async def fake_route(url, url_hash, text, word_count, content_length):
        routed.append(url)

    monkeypatch.setattr(w, "_route_to_stage4", fake_route)
    rec = asyncio.run(w._analyze_html("https://a.edu/big", "h", _html(60), False))
    assert rec["is_massive_doc"] is True and routed == ["https://a.edu/big"]


def test_intelligent_analyzer_uses_same_thresholds(monkeypatch):
    from src.stage2 import intelligent_analyzer as ia

    monkeypatch.setenv("STAGE2_MIN_WORD_COUNT", "7")
    monkeypatch.setenv("STAGE2_MIN_TEXT_TO_HTML_RATIO", "0.3")
    a = ia.IntelligentAnalyzer()
    try:
        assert (a.MIN_WORD_COUNT, a.MIN_TEXT_TO_HTML_RATIO, a.MASSIVE_DOC_THRESHOLD) == (7, 0.3, 50000)
    finally:
        a.client.close()
