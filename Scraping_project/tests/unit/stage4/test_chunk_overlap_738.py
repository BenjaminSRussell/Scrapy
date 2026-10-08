"""#738: Stage 4 chunking: valid overlap, fail-fast config, forward progress, adjacency-only overlap."""

import random
import signal
from unittest.mock import MagicMock, patch

import pytest

from src.stage4.large_doc_processor import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    LargeDocProcessor,
    chunk_spans,
    split_into_chunks,
    validate_chunking,
)


@pytest.fixture(autouse=True)
def _no_hang():
    # The pre-fix splitter looped forever on bad overlap; fail instead of hanging CI.
    def _boom(*_):
        raise TimeoutError("split_into_chunks did not terminate")
    old = signal.signal(signal.SIGALRM, _boom)
    signal.alarm(20)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)


def _texts():
    rng = random.Random(738)
    words = ["alpha", "beta", "gamma", "Δέλτα", "数据", "naïve", "emoji🙂", "x"]
    out = ["", " ", "a", "short text.", "x" * 100, "y" * 101]
    for n in (99, 100, 101, 250, 1000, 4321):
        out.append("".join(rng.choice(words) + rng.choice([" ", ". ", ", ", "\n"]) for _ in range(n)))
    out.append(".".join(["s"] * 300))   # a period every other char
    out.append("." * 500)               # periods only
    out.append("no periods at all " * 80)
    return out


CONFIGS = [(100, 0), (100, 1), (100, 30), (100, 49), (100, 50), (100, 51), (100, 90), (100, 99),
           (7, 6), (1, 0), (2, 1)]


@pytest.mark.parametrize("size,overlap", CONFIGS)
def test_progress_size_and_reconstruction(size, overlap):
    for text in _texts():
        spans = chunk_spans(text, size, overlap)
        chunks = split_into_chunks(text, size, overlap)
        assert chunks == [text[x:y].strip() for x, y in spans if text[x:y].strip()]
        if not text:
            assert spans == []
            continue
        assert spans[0][0] == 0 and spans[-1][1] == len(text)       # full coverage, nothing lost
        assert all(0 < y - x <= size for x, y in spans)             # size limit, non-empty
        for (x0, y0), (x1, y1) in zip(spans, spans[1:]):
            assert x1 > x0 and y1 > y0                              # strict forward progress
            assert x1 == y0 - overlap                               # exactly `overlap` shared, no gaps
        # Overlap only between neighbours: window k never reaches into window k+2.
        for (x0, y0), (x2, _) in zip(spans, spans[2:]):
            assert x2 >= y0 or overlap * 2 > size
        if not text.strip():
            assert chunks == []


def test_expected_overlap_between_adjacent_chunks():
    text = "".join(chr(ord("a") + i % 26) for i in range(1000))  # no periods, no whitespace
    chunks = split_into_chunks(text, 100, 20)
    for a, b in zip(chunks, chunks[1:]):
        assert a[-20:] == b[:20]
    assert len(chunks) == 13 and "".join([chunks[0]] + [c[20:] for c in chunks[1:]]) == text


def test_zero_overlap_is_a_partition():
    text = "abcdefghij" * 37
    chunks = split_into_chunks(text, 50, 0)
    assert "".join(chunks) == text


def test_sentence_snap_never_moves_backwards():
    # Old code: snap at a period just past size//2 with overlap > size//2 moved start backwards.
    text = ("a" * 52 + ". ") * 40
    chunks = split_into_chunks(text, 100, 60)
    assert chunks and all(len(c) <= 100 for c in chunks)


@pytest.mark.parametrize("size,overlap", [(100, 100), (100, 150), (100, -1), (0, 0), (-5, 0),
                                          ("100", 10), (100, 1.5), (True, 0), (100, None), (None, 10)])
def test_invalid_settings_fail_fast(size, overlap):
    with pytest.raises(ValueError, match="stage4 chunking"):
        validate_chunking(size, overlap)
    with pytest.raises(ValueError):
        split_into_chunks("x" * 1000, size, overlap)


def test_valid_settings_and_integral_floats():
    assert validate_chunking(100, 99) == (100, 99)
    assert validate_chunking(100.0, 10.0) == (100, 10)


def _processor(cfg):
    config = MagicMock()
    config.get.side_effect = lambda k, d=None: cfg.get(k, d)
    with patch("src.stage4.large_doc_processor.get_delta"), \
            patch("src.core.config.get_config", return_value=config):
        return LargeDocProcessor()


def test_processor_reads_config_and_defaults():
    p = _processor({"stage4.chunk_size": 800, "stage4.chunk_overlap": 80})
    assert (p.CHUNK_SIZE, p.OVERLAP) == (800, 80)
    assert all(len(c) <= 800 for c in p._split_into_chunks("word " * 1000))
    d = _processor({})
    assert (d.CHUNK_SIZE, d.OVERLAP) == (DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_OVERLAP)


def test_processor_rejects_bad_config_at_startup():
    with pytest.raises(ValueError, match="chunk_overlap must satisfy"):
        _processor({"stage4.chunk_size": 500, "stage4.chunk_overlap": 500})


def test_repo_config_is_valid():
    from pathlib import Path

    import yaml
    cfg = yaml.safe_load((Path(__file__).resolve().parents[3] / "config.yml").read_text())
    validate_chunking(cfg["stage4"]["chunk_size"], cfg["stage4"]["chunk_overlap"])
