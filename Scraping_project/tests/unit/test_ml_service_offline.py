"""Offline ml_service tests: no model download, no broker.

#296: request/response shaping with a stubbed transformers pipeline.
#485: readiness (/readyz 503 until warm-up + subscribe; no consume before warm-up).
#422: low-confidence review export to JSONL.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("confluent_kafka", reason="ml_service needs confluent_kafka (optional dep)")

from src import ml_service as ms  # noqa: E402
from src.schemas import CategoryType, LowConfidenceRecord  # noqa: E402


class StubPipeline:
    """Stands in for transformers.pipeline('zero-shot-classification')."""

    def __init__(self, ranking=None):
        self.calls: list[dict] = []
        self.ranking = ranking or [("admissions", 0.91), ("financial aid", 0.05), ("other", 0.04)]

    def __call__(self, text, labels, hypothesis_template, multi_label):
        self.calls.append({"text": text, "labels": labels, "template": hypothesis_template, "multi": multi_label})
        return {"labels": [label for label, _ in self.ranking], "scores": [score for _, score in self.ranking]}


@pytest.fixture
def stub(monkeypatch):
    holder = {}

    def fake_pipeline(task, model, device):
        assert task == "zero-shot-classification"
        holder["model"], holder["device"] = model, device
        holder["pipe"] = StubPipeline()
        return holder["pipe"]

    monkeypatch.setattr(ms, "pipeline", fake_pipeline)
    monkeypatch.setattr(ms, "TRANSFORMERS_AVAILABLE", True)
    return holder


# --- #296: classifier shaping ----------------------------------------------------


def test_classifier_maps_top_label_and_threshold(stub):
    clf = ms.ZeroShotClassifier(model_name="stub-model", confidence_threshold=0.85, device=-1)
    out = clf.classify("How do I apply?")
    assert stub["model"] == "stub-model" and stub["device"] == -1
    assert out["category"] is CategoryType.ADMISSIONS
    assert out["confidence"] == pytest.approx(0.91) and out["meets_threshold"] is True
    assert out["all_scores"]["financial aid"] == pytest.approx(0.05)
    call = stub["pipe"].calls[0]
    assert call["labels"] == clf.candidate_labels and call["template"] == clf.HYPOTHESIS_TEMPLATES[0]


def test_classifier_below_threshold_and_unknown_label(stub):
    clf = ms.ZeroShotClassifier(confidence_threshold=0.95)
    stub["pipe"].ranking = [("something new", 0.6), ("other", 0.4)]
    out = clf.classify("ambiguous")
    assert out["category"] is CategoryType.OTHER and out["meets_threshold"] is False


def test_empty_text_never_calls_model(stub):
    clf = ms.ZeroShotClassifier()
    out = clf.classify("   ")
    assert out == {"category": CategoryType.OTHER, "confidence": 0.0, "all_scores": {}, "meets_threshold": False}
    assert stub["pipe"].calls == []


def test_missing_transformers_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(ms, "TRANSFORMERS_AVAILABLE", False)
    with pytest.raises(ImportError, match="transformers"):
        ms.ZeroShotClassifier()


def test_extract_text_caps_content():
    svc = ms.ZSCMicroservice.__new__(ms.ZSCMicroservice)
    assert svc._extract_text({"title": "T", "content": "c" * 5000}) == "T " + "c" * 1000
    assert svc._extract_text({}) == ""


# --- #485: readiness ---------------------------------------------------------------


class _Consumer:
    def __init__(self, events, config=None):
        self.events = events
        self.config = config

    def subscribe(self, topics):
        self.events.append(("subscribe", topics))

    def poll(self, timeout=None):
        self.events.append(("poll",))
        raise KeyboardInterrupt

    def close(self):
        pass


class _Producer:
    def flush(self, timeout=None):
        return 0


def _get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_model_loads_and_warms_up_before_any_kafka_consume(monkeypatch, stub):
    events: list = []
    real_pipeline = ms.pipeline

    def tracking_pipeline(task, model, device):
        events.append(("load_model",))
        pipe = real_pipeline(task, model, device)
        orig = pipe.__call__

        class _P:
            def __call__(self, *a, **k):
                events.append(("inference",))
                return orig(*a, **k)

        return _P()

    monkeypatch.setattr(ms, "pipeline", tracking_pipeline)
    monkeypatch.setattr(ms, "Consumer", lambda cfg: _Consumer(events, cfg))
    monkeypatch.setattr(ms, "Producer", lambda cfg: _Producer())
    svc = ms.ZSCMicroservice()
    assert svc.classifier is None and events == []  # nothing heavy in __init__
    svc.start()
    kinds = [e[0] for e in events]
    assert kinds.index("load_model") < kinds.index("inference") < kinds.index("subscribe") < kinds.index("poll")


def test_readyz_is_503_until_ready_then_200():
    state = ms.ReadinessState()
    server = ms.start_health_server(state, port=0, host="127.0.0.1")
    port = server.server_address[1]
    try:
        assert _get(port, "/healthz")[0] == 200
        for phase in ("starting", "loading_model", "warming_up", "subscribing"):
            state.set(phase)
            code, body = _get(port, "/readyz")
            assert code == 503 and body["phase"] == phase
        state.set("ready")
        code, body = _get(port, "/readyz")
        assert code == 200 and body["ready"] is True
        assert _get(port, "/nope")[0] == 404
    finally:
        server.shutdown()


def test_warmup_failure_marks_not_ready_and_never_subscribes(monkeypatch, stub):
    events: list = []

    def broken_pipeline(task, model, device):
        raise OSError("model download blocked")

    monkeypatch.setattr(ms, "pipeline", broken_pipeline)
    monkeypatch.setattr(ms, "Consumer", lambda cfg: _Consumer(events, cfg))
    svc = ms.ZSCMicroservice()
    with pytest.raises(OSError):
        svc.start()
    assert svc.readiness.phase == "failed" and not svc.readiness.ready
    assert events == []


# --- #422: review export -------------------------------------------------------------


class _Msg:
    def __init__(self, value: bytes):
        self._v = value

    def value(self):
        return self._v

    def error(self):
        return None


class _ExportConsumer:
    def __init__(self, values):
        self.values = list(values)

    def poll(self, timeout=None):
        return _Msg(self.values.pop(0)) if self.values else None


def _record(i: int) -> bytes:
    return LowConfidenceRecord(
        url=f"https://uconn.edu/{i}", title=f"t{i}", content_preview="p", predicted_category=CategoryType.OTHER,
        confidence_score=0.4, threshold=0.85,
    ).model_dump_json().encode()


def test_review_export_writes_schema_rows_and_skips_garbage(tmp_path):
    out = tmp_path / "review.jsonl"
    consumer = _ExportConsumer([_record(1), b"not json", _record(2), b'{"url": "missing fields"}'])
    n = ms.export_low_confidence(consumer, out, max_records=10, idle_timeout=0.2)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert n == 2 and [r["url"] for r in rows] == ["https://uconn.edu/1", "https://uconn.edu/2"]
    assert set(rows[0]) == set(LowConfidenceRecord.model_fields)


def test_review_export_respects_limit(tmp_path):
    out = tmp_path / "review.jsonl"
    n = ms.export_low_confidence(_ExportConsumer([_record(i) for i in range(5)]), out, max_records=3, idle_timeout=0.2)
    assert n == 3 and len(out.read_text().splitlines()) == 3


def test_cli_exposes_review_export():
    root = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        [sys.executable, "cli.py", "ml", "review-export", "--help"], cwd=root, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0 and "--limit" in out.stdout and "--output" in out.stdout
