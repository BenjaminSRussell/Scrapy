"""src/stage4/summarization.py: length matrix, input validation, fallbacks (#232). Stub model only."""

from __future__ import annotations

import json
import sys
import types

import pytest

from src.core.constants import SUMMARY_LIMITS
from src.stage4 import summarization as sm

pytestmark = [pytest.mark.unit, pytest.mark.stage4]

LONG = " ".join(f"w{i}" for i in range(400))  # 400 words, ~2.1k chars


class Stub:
    def __init__(self, out="summary text", exc=None):
        self.calls: list[tuple[str, dict]] = []
        self.out, self.exc = out, exc

    def __call__(self, text, **kw):
        self.calls.append((text, kw))
        if self.exc:
            raise self.exc
        return [{"summary_text": self.out}]


@pytest.mark.parametrize("min_len,max_len", [(0, 1), (1, 1), (30, 150), (10, 60), (150, 150), (0, 1024)])
def test_valid_length_matrix_is_passed_to_the_model(min_len, max_len):
    s = Stub()
    assert sm.summarize_with_heavy_model(LONG, min_length=min_len, max_length=max_len, summarizer=s) == "summary text"
    (_, kw), = s.calls
    assert kw == {"max_length": max_len, "min_length": min_len, "do_sample": False}


def test_defaults_come_from_summary_limits():
    s = Stub()
    sm.summarize_with_heavy_model(LONG, summarizer=s)
    assert s.calls[0][1]["min_length"] == SUMMARY_LIMITS["min_length"]
    assert s.calls[0][1]["max_length"] == SUMMARY_LIMITS["max_length"]


@pytest.mark.parametrize(
    "min_len,max_len,match",
    [(31, 30, "must be <= max_length"), (-1, 10, "min_length must be >= 0"), (0, 0, "max_length must be >= 1"),
     (5, -3, "max_length must be >= 1"), (1.5, 10, "min_length must be an integer"),
     (1, "150", "max_length must be an integer"), (True, 10, "min_length must be an integer")],
)
def test_impossible_lengths_raise_before_the_model_is_touched(min_len, max_len, match):
    s = Stub()
    with pytest.raises(ValueError, match=match):
        sm.summarize_with_heavy_model(LONG, min_length=min_len, max_length=max_len, summarizer=s)
    assert s.calls == []


@pytest.mark.parametrize("bad", [None, b"bytes", 42, ["a"]])
def test_non_string_input_raises_type_error(bad):
    with pytest.raises(TypeError, match="text must be str"):
        sm.summarize_with_heavy_model(bad, summarizer=Stub())


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_blank_input_returns_empty_without_the_model(blank):
    s = Stub()
    assert sm.summarize_with_heavy_model(blank, summarizer=s) == ""
    assert s.calls == []


@pytest.mark.parametrize("words,called", [(29, False), (30, False), (31, True)])
def test_input_no_longer_than_min_length_is_returned_verbatim(words, called):
    """BART would have to invent text to reach min_length."""
    s = Stub()
    text = " ".join(["word"] * words)
    out = sm.summarize_with_heavy_model(text, min_length=30, max_length=150, summarizer=s)
    assert bool(s.calls) is called
    if not called:
        assert out == text


def test_input_is_truncated_to_chunk_size_at_a_word_boundary():
    s = Stub()
    text = "abcdefghi " * 500  # 5000 chars
    sm.summarize_with_heavy_model(text, summarizer=s)
    sent = s.calls[0][0]
    assert len(sent) <= SUMMARY_LIMITS["chunk_size"]
    assert sent.endswith("abcdefghi")  # never a half word


def test_truncate_helper_edges():
    assert sm._truncate_at_word("short", 10) == "short"
    assert sm._truncate_at_word("x" * 20, 10) == "x" * 10  # one long token: hard cut
    assert sm._truncate_at_word("aa bb cc", 5) == "aa"


@pytest.mark.parametrize("exc", [RuntimeError("cuda"), IndexError("empty output")])
def test_model_failure_falls_back_to_a_500_char_prefix(exc):
    out = sm.summarize_with_heavy_model(LONG, summarizer=Stub(exc=exc))
    assert out == LONG[:500] + "..."


def test_missing_transformers_falls_back(monkeypatch):
    monkeypatch.setitem(sys.modules, "transformers", None)
    assert sm.summarize_with_heavy_model(LONG) == LONG[:500] + "..."


def test_default_model_pipeline_is_built_lazily(monkeypatch):
    stub = Stub("lazy")
    built = []
    fake = types.SimpleNamespace(pipeline=lambda task, model, device: built.append((task, model, device)) or stub)
    monkeypatch.setitem(sys.modules, "transformers", fake)
    assert sm.summarize_with_heavy_model(LONG) == "lazy"
    assert built == [("summarization", "facebook/bart-large-cnn", -1)]


@pytest.mark.parametrize("min_len,max_len", [(0, 1), (30, 150), (150, 150)])
def test_validate_summary_lengths_accepts(min_len, max_len):
    assert sm.validate_summary_lengths(min_len, max_len) == (min_len, max_len)


# --- key facts / final summary / jsonl --------------------------------------------


def test_key_facts_prefer_category_sentences_and_cap_at_five():
    text = ". ".join([f"Research item {i}" for i in range(10)] + ["Unrelated"])
    facts = sm.extract_key_facts(text, "", ["research"])
    assert facts == [f"Research item {i}" for i in range(5)]


def test_key_facts_fall_back_to_first_three_sentences():
    assert sm.extract_key_facts("A. B. C. D.", "", ["zzz"]) == ["A", "B", "C"]
    assert sm.extract_key_facts("", "", []) == []


def test_final_summary_without_text_is_a_placeholder():
    out = sm.create_final_summary({"url": "u", "metadata": {"title": "T"}, "initial_categories": ["c"]})
    assert out == {"url": "u", "title": "T", "summary": "No content available", "key_facts": [], "categories": ["c"],
                   "type": "unknown"}


def test_final_summary_fields(monkeypatch):
    monkeypatch.setattr(sm, "summarize_with_heavy_model", lambda text: "SUM")
    out = sm.create_final_summary({
        "url": "u", "combined_text": "Alpha beta. Gamma delta.", "html_title": "H", "metadata": {"type": "pdf"},
        "initial_categories": ["gamma"], "ocr_texts": ["x"], "audio_transcripts": [], "video_transcripts": ["v"],
    })
    assert (out["summary"], out["title"], out["type"], out["word_count"]) == ("SUM", "H", "pdf", 4)
    assert out["key_facts"] == ["Gamma delta"]
    assert (out["has_ocr"], out["has_audio"], out["has_video"]) == (True, False, True)


def test_save_to_jsonl_appends_utf8(tmp_path):
    f = tmp_path / "sub" / "out.jsonl"
    sm.save_to_jsonl([{"t": "café"}], f)
    sm.save_to_jsonl([{"t": "2"}], f)
    lines = f.read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["t"] for x in lines] == ["café", "2"]
    assert "café" in lines[0]  # ensure_ascii=False
