"""Sentence-aware, Unicode-safe truncation with a hard length limit (#740).

``truncate_text(text, limit)`` never returns more than ``limit`` characters
(code points), ellipsis included:

1. Text that already fits is returned unchanged (including ``""``).
2. Otherwise it ends at the last complete sentence (``. ! ? …`` and CJK ``。！？``
   followed by whitespace/closing quote) that keeps at least ``min_ratio`` of
   the budget; common abbreviations (``e.g.``, ``Dr.``, ``U.S.``, initials) are
   not treated as sentence ends. No ellipsis is added after a full sentence.
3. Else it cuts at the last word boundary and appends the ellipsis.
4. Else (one long word) it hard-cuts, never splitting a combining mark, a
   variation selector or a ZWJ sequence from its base character.

The result is deterministic for a given input.
"""

from __future__ import annotations

import re
import unicodedata

ELLIPSIS = "…"
_SENTENCE_END = re.compile(r"[.!?…。！？][\"'”’)\]]*(?=\s|$)")
_ABBREVIATIONS = frozenset({
    "e.g", "i.e", "etc", "vs", "dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "no", "fig",
    "u.s", "u.k", "a.m", "p.m", "inc", "ltd", "co", "dept", "univ", "approx", "al",
})
_JOINERS = {"\u200d"}  # ZERO WIDTH JOINER


def _is_abbreviation(text: str, dot_index: int) -> bool:
    if text[dot_index] != ".":
        return False
    start = dot_index
    while start > 0 and (text[start - 1].isalpha() or text[start - 1] == "."):
        start -= 1
    word = text[start:dot_index].lower()
    if not word:
        return False
    if len(word) == 1 and word.isalpha():  # initials: "J. Smith"
        return True
    return word in _ABBREVIATIONS


def _attached(ch: str) -> bool:
    """True if ``ch`` must stay with the preceding character."""
    return (unicodedata.combining(ch) != 0 or ch in _JOINERS
            or "\ufe00" <= ch <= "\ufe0f" or "\U0001f3fb" <= ch <= "\U0001f3ff")


def safe_cut(text: str, index: int) -> int:
    """Largest cut point ``<= index`` that does not split a grapheme cluster we can detect."""
    index = max(0, min(index, len(text)))
    while 0 < index < len(text) and (_attached(text[index]) or text[index - 1] in _JOINERS):
        index -= 1
    return index


def truncate_text(text: str | None, limit: int, *, ellipsis: str = ELLIPSIS, min_ratio: float = 0.5) -> str:
    text = "" if text is None else str(text)
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    budget = limit - len(ellipsis)
    if budget <= 0:
        return text[:safe_cut(text, limit)]

    # 2) last complete sentence inside the limit (no ellipsis needed)
    best = -1
    for m in _SENTENCE_END.finditer(text, 0, limit + 1):
        end = m.end()
        if end > limit:
            break
        if _is_abbreviation(text, m.start()):
            continue
        best = end
    if best >= max(1, int(budget * min_ratio)):
        return text[:best].rstrip()

    # 3) last word boundary within the budget
    window = text[:budget + 1]
    ws = max(window.rfind(" "), window.rfind("\n"), window.rfind("\t"))
    if ws > 0 and text[:ws].strip():
        return text[:ws].rstrip() + ellipsis

    # 4) hard cut, grapheme-safe
    return text[:safe_cut(text, budget)] + ellipsis
