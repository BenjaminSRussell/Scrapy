"""#429: ASR never sends audio to Google unless ASR_PROVIDER=google is set explicitly."""

from __future__ import annotations

import logging
import sys
import types

import pytest

pytest.importorskip("twisted")

from src.common import async_asr_processor as asr  # noqa: E402


class _Recognizer:
    calls: list[tuple[str, str]] = []

    def record(self, source):
        return types.SimpleNamespace(frame_data=b"\0" * 32000, sample_rate=16000)

    def recognize_google(self, audio, language):
        _Recognizer.calls.append(("google", language))
        return "from google"

    def recognize_whisper(self, audio, language):
        _Recognizer.calls.append(("whisper", language))
        return "from whisper"


class _AudioFile:
    def __init__(self, path):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_sr(monkeypatch):
    _Recognizer.calls = []
    fake = types.SimpleNamespace(
        Recognizer=_Recognizer,
        AudioFile=_AudioFile,
        UnknownValueError=type("UnknownValueError", (Exception,), {}),
        RequestError=type("RequestError", (Exception,), {}),
    )
    monkeypatch.setattr(asr, "sr", fake)
    monkeypatch.setattr(asr, "SPEECH_RECOGNITION_AVAILABLE", True)
    monkeypatch.setattr(asr, "REQUESTS_AVAILABLE", True)
    return _Recognizer


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ASR_PROVIDER", raising=False)
    monkeypatch.setattr(asr, "_google_warning_logged", False)


def _proc(tmp_path, provider=None):
    p = asr.AsyncASRProcessor(max_workers=1, temp_dir=str(tmp_path), provider=provider)
    p.executor.shutdown(wait=False)
    return p


def test_default_provider_is_none():
    assert asr.resolve_asr_provider() == "none"


@pytest.mark.parametrize("raw,expected", [("google", "google"), (" Whisper ", "whisper"), ("NONE", "none"), ("gogle", "none"), ("", "none")])
def test_resolve_provider(raw, expected):
    assert asr.resolve_asr_provider(raw) == expected


def test_env_var_opt_in(monkeypatch):
    monkeypatch.setenv("ASR_PROVIDER", "google")
    assert asr.resolve_asr_provider() == "google"


def test_default_transcription_never_calls_google(fake_sr):
    out = asr.transcribe_audio_file("x.wav")
    assert out["success"] is False and "disabled" in out["error"]
    assert fake_sr.calls == []


def test_google_only_when_explicit(fake_sr):
    out = asr.transcribe_audio_file("x.wav", provider="google")
    assert out["transcript"] == "from google"
    assert fake_sr.calls == [("google", "en-US")]


def test_whisper_is_local_and_gets_iso_language(fake_sr):
    out = asr.transcribe_audio_file("x.wav", language="en-US", provider="whisper")
    assert out["transcript"] == "from whisper"
    assert fake_sr.calls == [("whisper", "en")]


def test_default_processor_does_not_download(fake_sr, tmp_path, monkeypatch):
    proc = _proc(tmp_path)
    assert proc.provider == "none"
    monkeypatch.setattr(proc, "_download_media", lambda url: pytest.fail("downloaded with ASR disabled"))
    item = {"media_url": "https://media.example.edu/talk.wav"}
    out = {}
    proc.process_media_url(item["media_url"], item).addCallback(lambda r: out.setdefault("ok", r))
    assert out and "transcript" not in item


def test_processor_passes_provider_to_worker(fake_sr, tmp_path):
    proc = _proc(tmp_path, provider="whisper")
    seen = {}

    class _Exec:
        def submit(self, fn, path):
            seen["result"] = fn(path)
            from concurrent.futures import Future

            f: Future = Future()
            f.set_result(seen["result"])
            return f

        def shutdown(self, wait=True):
            pass

    proc.executor = _Exec()
    media = tmp_path / "talk.wav"
    media.write_bytes(b"RIFF")
    proc._transcribe_async(str(media), {})
    assert seen["result"]["transcript"] == "from whisper"
    assert fake_sr.calls == [("whisper", "en")]


def test_google_warning_logged_once(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=asr.logger.name):
        _proc(tmp_path, provider="google")
        _proc(tmp_path, provider="google")
    warnings = [r for r in caplog.records if "Google" in r.getMessage()]
    assert len(warnings) == 1


def test_settings_default_is_none(monkeypatch):
    monkeypatch.delenv("ASR_PROVIDER", raising=False)
    sys.modules.pop("src.settings", None)
    import src.settings as settings

    assert settings.ASR_PROVIDER == "none"
