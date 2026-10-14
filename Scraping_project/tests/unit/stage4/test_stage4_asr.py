"""Stage 4 calls async_asr_processor only when STAGE4_ASR_ENABLED is on (#1101)."""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

from src.common import async_asr_processor as asr
from src.stage4 import summarization as sm
from src.stage4.stage4_worker import _fold_asr_text

pytestmark = [pytest.mark.unit, pytest.mark.stage4]


def _sample(name: str) -> float:
    return REGISTRY.get_sample_value(name) or 0.0


def _stub_summary(monkeypatch):
    seen: dict[str, str] = {}

    def capture(text, **kwargs):
        seen["text"] = text
        return "SUM"

    monkeypatch.setattr(sm, "summarize_with_heavy_model", capture)
    return seen


def _fixtures(tmp_path):
    audio = tmp_path / "talk.wav"
    audio.write_bytes(b"RIFF-not-a-real-wav")
    text = tmp_path / "notes.txt"
    text.write_text("written aid policy\n", encoding="utf-8")
    return audio, text


def _record(audio, text, **extra):
    data = {
        "url": "https://example.edu/aid",
        "combined_text": "",
        "metadata": {"title": "Aid"},
        "initial_categories": [],
        "audio_path": str(audio),
        "text_path": str(text),
    }
    data.update(extra)
    return data


@pytest.fixture
def flag_off(monkeypatch):
    monkeypatch.setenv("STAGE4_ASR_ENABLED", "0")
    monkeypatch.delenv("ASR_PROVIDER", raising=False)


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv("STAGE4_ASR_ENABLED", "1")
    monkeypatch.delenv("ASR_PROVIDER", raising=False)


def test_flag_off_makes_zero_asr_calls(flag_off, tmp_path, monkeypatch):
    audio, text = _fixtures(tmp_path)
    calls = []

    def boom(*args, **kwargs):
        calls.append(args)
        raise AssertionError("ASR was called while STAGE4_ASR_ENABLED is off")

    monkeypatch.setattr(asr, "transcribe_audio_file", boom)
    jobs = _sample("asr_jobs_total")
    out = sm.create_final_summary(_record(audio, text))
    assert calls == []
    assert out["summary"] == "No content available"
    assert "written aid policy" not in str(out)
    assert _sample("asr_jobs_total") == jobs
    assert _fold_asr_text(_record(audio, text), "body") == "body"


def test_flag_on_processes_audio_and_text_fixtures(flag_on, tmp_path, monkeypatch):
    audio, text = _fixtures(tmp_path)
    calls = []

    def fake(path, language="en-US", provider="none"):
        calls.append((path, provider))
        return {"success": True, "transcript": "spoken aid deadline", "error": None, "duration": 1.0}

    monkeypatch.setattr(asr, "transcribe_audio_file", fake)
    monkeypatch.setattr(asr, "SPEECH_RECOGNITION_AVAILABLE", True)
    seen = _stub_summary(monkeypatch)
    jobs, failures, latency = _sample("asr_jobs_total"), _sample("asr_failures_total"), _sample("asr_latency_count")
    out = sm.create_final_summary(_record(audio, text))
    assert calls == [(str(audio), "whisper")]
    assert out["summary"] == "SUM"
    assert out["has_audio"] is True
    assert "spoken aid deadline" in seen["text"]
    assert "written aid policy" in seen["text"]
    assert _sample("asr_jobs_total") == jobs + 1
    assert _sample("asr_failures_total") == failures
    assert _sample("asr_latency_count") == latency + 1
    folded = _fold_asr_text({"text_path": str(text), "audio_path": str(audio)}, "body")
    assert folded.startswith("body\n")
    assert "written aid policy" in folded and "spoken aid deadline" in folded


def test_flag_on_skips_when_speech_library_is_missing(flag_on, tmp_path, monkeypatch):
    audio, text = _fixtures(tmp_path)
    calls = []
    monkeypatch.setattr(asr, "transcribe_audio_file", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(asr, "SPEECH_RECOGNITION_AVAILABLE", False)
    monkeypatch.setattr(asr, "_missing_speech_logged", False)
    seen = _stub_summary(monkeypatch)
    jobs, failures = _sample("asr_jobs_total"), _sample("asr_failures_total")
    out = sm.create_final_summary(_record(audio, text, combined_text="Page body already here."))
    assert calls == []
    assert out["summary"] == "SUM"
    assert "Page body already here." in seen["text"]
    assert "written aid policy" in seen["text"]
    assert out["has_audio"] is True  # the text fixture is still a transcript
    assert _sample("asr_jobs_total") == jobs
    assert _sample("asr_failures_total") == failures


def test_provider_none_skips_audio_but_keeps_text(flag_on, tmp_path, monkeypatch):
    audio, text = _fixtures(tmp_path)
    calls = []
    monkeypatch.setenv("ASR_PROVIDER", "none")
    monkeypatch.setattr(asr, "transcribe_audio_file", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(asr, "SPEECH_RECOGNITION_AVAILABLE", True)
    seen = _stub_summary(monkeypatch)
    out = sm.create_final_summary(_record(audio, text))
    assert calls == []
    assert out["summary"] == "SUM"
    assert seen["text"] == "written aid policy"
    assert out["has_audio"] is True


def test_remote_media_url_is_not_downloaded(flag_on, tmp_path, monkeypatch):
    _audio, text = _fixtures(tmp_path)
    calls = []
    monkeypatch.setattr(asr, "transcribe_audio_file", lambda *a, **k: calls.append(a))
    seen = _stub_summary(monkeypatch)
    record = {
        "url": "https://example.edu/talk.wav",
        "combined_text": "Already fetched page text for the summary path.",
        "metadata": {"title": "T"},
        "initial_categories": [],
        "audio_paths": ["https://example.edu/talk.wav"],
        "text_path": str(text),
    }
    out = sm.create_final_summary(record)
    assert calls == []
    assert out["summary"] == "SUM"
    assert "Already fetched page text" in seen["text"]
    assert "written aid policy" in seen["text"]


def test_transcription_error_counts_a_failure_and_keeps_other_text(flag_on, tmp_path, monkeypatch):
    audio, text = _fixtures(tmp_path)
    text.write_text("", encoding="utf-8")

    def fake(path, language="en-US", provider="none"):
        return {"success": False, "transcript": "", "error": "garbled", "duration": 0}

    monkeypatch.setattr(asr, "transcribe_audio_file", fake)
    monkeypatch.setattr(asr, "SPEECH_RECOGNITION_AVAILABLE", True)
    seen = _stub_summary(monkeypatch)
    failures = _sample("asr_failures_total")
    jobs = _sample("asr_jobs_total")
    out = sm.create_final_summary(_record(audio, text, combined_text="Keep this page."))
    assert out["summary"] == "SUM"
    assert seen["text"] == "Keep this page."
    assert out["has_audio"] is False
    assert _sample("asr_jobs_total") == jobs + 1
    assert _sample("asr_failures_total") == failures + 1
