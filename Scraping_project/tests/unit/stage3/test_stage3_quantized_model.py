"""Offline smoke for the Stage 3 quantized model option (#652).

No torch, no network, no weight files. The default worker variant stays full.
"""

from __future__ import annotations

import asyncio
import re
import socket
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.stage3 import quantized_model as q
from src.stage3.stage3_worker import MAX_SUMMARY_CHARS, Stage3Worker

pytestmark = [pytest.mark.unit, pytest.mark.stage3, pytest.mark.smoke]

ROOT = Path(__file__).resolve().parents[3]
_BANNED_IMPORTS = ("torch", "transformers", "sentence_transformers", "llama_cpp")


def _forbid_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("socket opened")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    if hasattr(socket, "create_server"):
        monkeypatch.setattr(socket, "create_server", boom)


def _bare_worker(variant: str) -> Stage3Worker:
    worker = Stage3Worker.__new__(Stage3Worker)
    worker.semaphore = asyncio.Semaphore(1)
    worker.model_variant = variant
    worker.postgres = MagicMock(name="postgres")
    return worker


def _doc(text: str) -> dict[str, Any]:
    return {
        "url": "https://example.edu/page",
        "url_hash": "h-page",
        "text_content": text,
        "keywords": ["research"],
        "quality_score": 80,
    }


def test_quantized_smoke_is_offline_and_documents_caveats(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Path(q.__file__).read_text(encoding="utf-8")
    worker_source = (ROOT / "src/stage3/stage3_worker.py").read_text(encoding="utf-8")
    for banned in _BANNED_IMPORTS:
        pattern = re.compile(rf"^\s*(?:import|from)\s+{banned}\b", re.MULTILINE)
        assert pattern.search(source) is None
        assert pattern.search(worker_source) is None

    before = {name: name in sys.modules for name in _BANNED_IMPORTS}
    _forbid_sockets(monkeypatch)
    text = "Alpha sentence. Beta sentence. Gamma sentence. Delta sentence."
    result = q.run_quantized_smoke(text, max_chars=MAX_SUMMARY_CHARS)

    assert {name: name in sys.modules for name in _BANNED_IMPORTS} == before
    assert result["variant"] == q.VARIANT_QUANTIZED
    assert result["format"] == "int8-gguf"
    assert result["model_id"].endswith("distilbart-cnn-12-6-q8_0.gguf")
    assert result["summary"] == "Alpha sentence. Beta sentence."
    assert "not citation-grade" in result["quality_caveat"]
    assert "1222317369" in result["image_size_note"]
    assert "requirements-stage4.txt" in result["image_size_note"]
    assert "No image was rebuilt" in result["image_size_note"]

    extras = (ROOT / "docs/OPTIONAL_EXTRAS.md").read_text(encoding="utf-8")
    assert q.QUALITY_CAVEAT in extras
    assert q.IMAGE_SIZE_NOTE in extras
    config = (ROOT / "config.yml").read_text(encoding="utf-8")
    assert "model_variant: full" in config
    assert "quantized" in config


def test_only_quantized_string_opts_in() -> None:
    assert q.normalize_variant(None) == q.VARIANT_FULL
    assert q.normalize_variant("full") == q.VARIANT_FULL
    assert q.normalize_variant("  FULL ") == q.VARIANT_FULL
    assert q.normalize_variant("quantized") == q.VARIANT_QUANTIZED
    assert q.normalize_variant(" Quantized ") == q.VARIANT_QUANTIZED
    for raw in (True, False, 1, "yes", "int8", "gguf", "cuda"):
        assert q.normalize_variant(raw) == q.VARIANT_FULL


def test_shipped_config_stays_full() -> None:
    assert q.configured_variant() == q.VARIANT_FULL


def test_worker_quantized_path_uses_stub_and_full_path_does_not() -> None:
    text = "One. Two. Three. Four. Five. Six."
    quantized = _bare_worker(q.VARIANT_QUANTIZED)
    full = _bare_worker(q.VARIANT_FULL)
    unset = _bare_worker(q.VARIANT_FULL)
    del unset.model_variant

    q_out = asyncio.run(quantized._summarize_document(_doc(text)))
    f_out = asyncio.run(full._summarize_document(_doc(text)))
    u_out = asyncio.run(unset._summarize_document(_doc(text)))

    assert q_out is not None and f_out is not None and u_out is not None
    assert q_out["summary"] == "One. Two."
    assert f_out["summary"] == u_out["summary"]
    assert f_out["summary"].startswith("One. Two. Three.")
    assert q_out["summary"] != f_out["summary"]
    assert q_out["url_hash"] == "h-page"
    assert "model_variant" not in q_out
