"""Bounds and edge cases: backoff (#722), stage4 chunking (#738), stage2 empty bodies (#739),
stage3 truncation (#740)."""
from __future__ import annotations

import asyncio
import random
import unicodedata

import pytest

from src.stage4.large_doc_processor import chunk_spans, validate_chunking
from src.utils.text_truncate import ELLIPSIS, safe_cut, truncate_text

# --- #738 stage4 chunking ------------------------------------------------------------------

TEXTS = {
    "empty": "",
    "short": "One sentence.",
    "exact": "x" * 100,
    "exact_plus_one": "x" * 101,
    "no_periods": "word " * 400,
    "periods": " ".join(f"Sentence number {i} ends here." for i in range(120)),
    "unicode": "Grüße aus Storrs. 東京の夜。" * 60 + "naïve café ☕️ " * 50,
    "ends_on_boundary": "a" * 90 + "b" * 90,  # previously produced a duplicate tail chunk
}


@pytest.mark.parametrize("name", sorted(TEXTS))
@pytest.mark.parametrize("size,overlap", [(100, 0), (100, 10), (100, 50), (37, 18), (5000, 500)])
def test_chunk_spans_properties(name, size, overlap):
    text = TEXTS[name]
    spans = chunk_spans(text, size, overlap)
    if not text:
        assert spans == []
        return
    assert spans[0][0] == 0 and spans[-1][1] == len(text)  # full coverage
    for i, (a, b) in enumerate(spans):
        assert 0 < b - a <= size  # size limit, non-empty
        if i:
            pa, pb = spans[i - 1]
            assert a > pa  # forward progress
            assert a == pb - overlap  # exactly `overlap` shared with the previous chunk
        if i >= 2:
            assert a >= spans[i - 2][1]  # nothing shared with non-adjacent chunks
    if len(spans) > 1:
        assert spans[-1][1] - spans[-1][0] > overlap  # last chunk is not just the overlap


def test_chunk_spans_prefers_sentence_ends():
    text = TEXTS["periods"]
    spans = chunk_spans(text, 200, 20)
    assert all(text[b - 1] == "." for _, b in spans[:-1])


def test_boundary_text_no_duplicate_tail():
    # 180 chars, size 100, overlap 10: [0,100) then [90,180) -- and nothing inside [170,180)
    assert chunk_spans(TEXTS["ends_on_boundary"], 100, 10) == [(0, 100), (90, 180)]


@pytest.mark.parametrize("size,overlap", [(100, 100), (100, 51), (100, 150), (0, 0), (-5, 0), (100, -1),
                                          (100.0, 10), (100, 1.5), (True, 0)])
def test_invalid_chunk_settings_fail_fast(size, overlap):
    with pytest.raises(ValueError):
        validate_chunking(size, overlap)
    with pytest.raises(ValueError):
        chunk_spans("x" * 1000, size, overlap)


def test_processor_split_matches_spans_and_drops_blank_chunks():
    from src.stage4.large_doc_processor import LargeDocProcessor

    p = LargeDocProcessor.__new__(LargeDocProcessor)
    p.CHUNK_SIZE, p.OVERLAP = 100, 10
    text = "a" * 95 + " " * 200 + "b" * 50
    chunks = p._split_into_chunks(text)
    assert chunks and all(c.strip() for c in chunks) and all(len(c) <= 100 for c in chunks)
    assert p._split_into_chunks("short") == ["short"]


# --- #740 stage3 truncation ----------------------------------------------------------------

def _graphemes_intact(original: str, out: str) -> bool:
    body = out[:-1] if out.endswith(ELLIPSIS) else out
    nxt = original[len(body):len(body) + 1]
    return not (nxt and (unicodedata.combining(nxt) or nxt in "\u200d\ufe0f"))


@pytest.mark.parametrize("text", [
    "", "Short.", "Hello world. This is a long sentence that goes on and on.",
    "Dr. Smith met Prof. Jones at 3 p.m. in the U.S. capital. They talked for hours about it.",
    "Supercalifragilisticexpialidocious" * 5, "no terminator at all just words " * 10,
    "Café crème. Naïve résumé! Где ты? 東京に行きました。次は大阪です。",
    "e\u0301" * 40, "👩\u200d👩\u200d👧" * 20, "☕\ufe0f" * 30,
])
@pytest.mark.parametrize("limit", [1, 2, 5, 10, 25, 40, 500])
def test_truncate_never_exceeds_limit_and_is_deterministic(text, limit):
    out = truncate_text(text, limit)
    assert len(out) <= limit
    assert out == truncate_text(text, limit)
    if len(text) <= limit:
        assert out == text  # short and empty text unchanged
    else:
        assert _graphemes_intact(text, out)


def test_truncate_ends_at_complete_sentence_when_possible():
    text = "The library opens at nine. Staff arrive earlier to prepare. Visitors queue outside."
    assert truncate_text(text, 70) == "The library opens at nine. Staff arrive earlier to prepare."
    # abbreviations are not sentence ends
    text = "Dr. Smith met Prof. Jones at 3 p.m. in the U.S. capital today. They talked."
    assert truncate_text(text, 70) == "Dr. Smith met Prof. Jones at 3 p.m. in the U.S. capital today."


def test_truncate_word_and_hard_fallbacks():
    assert truncate_text("alpha beta gamma delta epsilon", 17) == "alpha beta gamma" + ELLIPSIS
    assert truncate_text("x" * 50, 10) == "x" * 9 + ELLIPSIS
    assert truncate_text("abc", 0) == "" and truncate_text(None, 5) == ""
    assert safe_cut("e\u0301e\u0301", 1) == 0 and safe_cut("e\u0301e\u0301", 2) == 2


def test_stage3_fallback_summary_respects_limit():
    from src.stage3.stage3_worker import Stage3Worker

    w = Stage3Worker.__new__(Stage3Worker)
    long = "First point is clear. " * 40
    out = w._fallback_summary(long, max_chars=100)
    assert len(out) <= 100 and out.endswith(".")
    assert w._fallback_summary("tiny", max_chars=100) == "tiny"


# --- #722 backoff bounds ---------------------------------------------------------------------

def _middleware(base=2, cap=300):
    from unittest.mock import MagicMock

    from src.stage1.middlewares.retry_middleware import IntelligentRetryMiddleware

    settings = MagicMock()
    settings.getint.side_effect = lambda key, default=None: {"RETRY_BACKOFF_BASE": base,
                                                             "RETRY_BACKOFF_MAX": cap}.get(key, default)
    settings.getbool.return_value = True
    settings.getlist.return_value = []
    settings.get.return_value = None
    return IntelligentRetryMiddleware(settings)


@pytest.mark.parametrize("attempt", [0, 1, 3, 8, 9, 50, 2000, 10**6])
def test_middleware_backoff_bounded_and_capped(attempt):
    m = _middleware(base=2, cap=300)
    raw = 300.0 if attempt > 20 else min(2.0 ** attempt, 300.0)
    for _ in range(50):
        d = m._compute_backoff(attempt)
        assert raw <= d <= min(raw * 1.1, 300.0) + 1e-9


def test_middleware_backoff_seeded_rng_is_repeatable():
    a, b = _middleware(), _middleware()
    assert [a._compute_backoff(i) for i in range(1, 8)] == [b._compute_backoff(i) for i in range(1, 8)]
    a._rng = random.Random(7)
    b._rng = random.Random(7)
    assert [a._compute_backoff(5) for _ in range(5)] == [b._compute_backoff(5) for _ in range(5)]


def test_stage2_retry_delay_bounds_cap_and_seed():
    from src.stage2.stage2_worker import Stage2Worker

    w = Stage2Worker.__new__(Stage2Worker)
    w.http_backoff_base, w.http_backoff_max = 0.5, 8.0
    for attempt in (1, 2, 4, 5, 6, 40):  # first, middle, capped
        base = min(8.0, 0.5 * 2 ** (attempt - 1))
        for _ in range(50):
            assert base / 2 <= w._retry_delay(attempt) <= base <= 8.0
    assert w._retry_delay(1, retry_after=1e9) == 8.0
    random.seed(1234)
    first = [w._retry_delay(3) for _ in range(5)]
    random.seed(1234)
    assert first == [w._retry_delay(3) for _ in range(5)]


# --- #739 stage2 empty bodies ----------------------------------------------------------------

EMPTY_BODIES = {
    "zero_bytes": "",
    "whitespace": "   \n\t  \r\n",
    "empty_body": "<html><head><title>x</title></head><body></body></html>",
    "comments_only": "<html><body><!-- nothing here --><!-- still nothing --></body></html>",
    "scripts_only": "<html><body><script>var a = 1;</script><style>p{}</style></body></html>",
    "nav_chrome_only": "<html><body><nav>Home About</nav><footer>(c) 2026</footer></body></html>",
}


@pytest.fixture
def stage2():
    from src.stage2.stage2_worker import Stage2Worker

    w = Stage2Worker.__new__(Stage2Worker)
    w.MIN_WORD_COUNT, w.MIN_TEXT_TO_HTML_RATIO, w.MASSIVE_DOC_THRESHOLD = 50, 0.1, 50000
    routed = []

    async def no_route(*a, **k):
        routed.append(a)

    w._route_to_stage4 = no_route
    w.routed = routed
    return w


@pytest.mark.parametrize("name", sorted(EMPTY_BODIES))
def test_empty_bodies_are_low_quality_not_errors_and_not_persisted_as_content(stage2, name):
    row = asyncio.run(stage2._analyze_html("https://uconn.edu/e", "h", EMPTY_BODIES[name], False))
    assert row["is_low_quality"] is True
    assert row["text_content"] == ""  # no blank text stored as content
    assert row["has_error"] is False  # classified as a completed low-quality page: no retry
    assert row["word_count"] == 0 or name == "empty_body"
    assert row["quality_score"] <= 0.01 and row["keywords"] == [""]
    assert row["is_massive_doc"] is False and stage2.routed == []


def test_minimal_valid_page_still_passes(stage2):
    words = " ".join(f"word{i}" for i in range(80))
    html = f"<html><head><title>Ok</title></head><body><p>{words}</p></body></html>"
    row = asyncio.run(stage2._analyze_html("https://uconn.edu/ok", "h", html, False))
    assert row["is_low_quality"] is False and "word0 word1" in row["text_content"]
    assert row["title"] == "Ok" and row["has_error"] is False
