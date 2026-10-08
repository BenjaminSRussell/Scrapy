"""Adapt Stage 1 crawling to noisy site sections (#26).

A *section* is ``host/first-path-segment`` of the page being parsed
(``events.uconn.edu/calendar``). For every parsed page the scout reports how
many follow-able HTML links it found and how many of them look
low-confidence. Once a section has produced at least ``min_pages`` pages and
its low-confidence share reaches ``low_value_ratio``, it is *noisy*: links from
its pages that are low-confidence are no longer followed, the rest are capped
at ``follow_cap`` per page and requested at a lower priority.

A link is low-confidence when URLValueAssessor (regex-only, no history) scores
it "low", or it looks like a crawler trap: dated archive paths, calendar or
facet/sort/filter query parameters, session ids, many query parameters, or a
repeating path. Sections recover automatically if later pages are cleaner.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlparse

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Gauge

    NOISY_SECTION_SKIPS: Any = Counter(
        "stage1_noisy_section_skips_total",
        "Links not followed because their source section is noisy (#26).",
    )
    NOISY_SECTIONS: Any = Gauge("stage1_noisy_sections", "Sections currently classed as noisy (#26).")
except Exception:  # prometheus_client missing or already registered
    NOISY_SECTION_SKIPS = None
    NOISY_SECTIONS = None

_DATED_PATH = re.compile(r"/(19|20)\d{2}/(0?[1-9]|1[0-2])(/(0?[1-9]|[12]\d|3[01]))?(/|$)")
_TRAP_PARAMS = frozenset(
    {
        "date", "day", "month", "year", "week", "start", "end", "time",
        "sort", "order", "orderby", "sortby", "dir", "filter", "facet", "view",
        "display", "layout", "format", "print", "share", "replytocom",
        "sessionid", "sid", "phpsessid", "jsessionid", "utm_source", "utm_medium", "utm_campaign",
    }
)
_MAX_PARAMS = 3


@dataclass
class _Section:
    pages: int = 0
    links: int = 0
    low: int = 0
    noisy: bool = False


def section_of(url: str) -> str:
    parsed = urlparse(url)
    segment = next((s for s in parsed.path.split("/") if s), "")
    return f"{parsed.netloc.lower()}/{segment.lower()}"


def looks_like_trap(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    if _DATED_PATH.search(path):
        return True
    params = parse_qsl(parsed.query, keep_blank_values=True)
    if len(params) > _MAX_PARAMS:
        return True
    if any(k.lower() in _TRAP_PARAMS for k, _ in params):
        return True
    segments = [s for s in path.split("/") if s]
    if len(segments) >= 4 and len(set(segments)) <= len(segments) // 2:
        return True  # /a/b/a/b/a/b relative-link loops
    return False


class SectionNoiseTracker:
    def __init__(
        self,
        enabled: bool = True,
        min_pages: int = 5,
        low_value_ratio: float = 0.6,
        follow_cap: int = 10,
        assessor: Any = None,
    ) -> None:
        self.enabled = enabled
        self.min_pages = max(1, int(min_pages))
        self.low_value_ratio = float(low_value_ratio)
        self.follow_cap = max(0, int(follow_cap))
        self._assessor = assessor
        self._sections: dict[str, _Section] = {}
        self.skipped = 0

    @classmethod
    def from_config(cls, config: Any) -> SectionNoiseTracker:
        block = None
        for key in ("stage1.noisy_sections", "stages.stage1.noisy_sections"):
            try:
                block = config.get(key)
            except Exception:
                block = None
            if block is not None:
                break
        if not isinstance(block, dict):
            return cls()
        enabled = block.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() in ("1", "true", "yes", "on")
        return cls(
            enabled=bool(enabled),
            min_pages=block.get("min_pages", 5),
            low_value_ratio=block.get("low_value_ratio", 0.6),
            follow_cap=block.get("follow_cap", 10),
        )

    def _assess(self) -> Any:
        if self._assessor is None:
            from src.common.url_value_assessor import URLValueAssessor

            self._assessor = URLValueAssessor(use_historical_data=False)
        return self._assessor

    def is_low_value(self, url: str) -> bool:
        if looks_like_trap(url):
            return True
        try:
            likelihood: str = self._assess().assess_url(url).content_likelihood
            return likelihood == "low"
        except Exception as e:  # assessment is advisory; never break parsing
            logger.debug(f"[NOISE] assess_url failed for {url[:80]}: {e}")
            return False

    def is_noisy(self, section: str) -> bool:
        return self.enabled and self._sections.get(section, _Section()).noisy

    def should_follow(self, section: str, url: str, followed_so_far: int, low_value: bool) -> bool:
        if not self.is_noisy(section):
            return True
        if low_value or followed_so_far >= self.follow_cap:
            self.skipped += 1
            if NOISY_SECTION_SKIPS is not None:
                NOISY_SECTION_SKIPS.inc()
            return False
        return True

    def record_page(self, section: str, links: int, low: int) -> None:
        if not self.enabled:
            return
        state = self._sections.setdefault(section, _Section())
        state.pages += 1
        state.links += links
        state.low += low
        was = state.noisy
        state.noisy = state.pages >= self.min_pages and state.links > 0 and state.low / state.links >= self.low_value_ratio
        if state.noisy != was:
            logger.info(
                f"[NOISE] Section {section} {'is now noisy' if state.noisy else 'recovered'}: "
                f"{state.low}/{state.links} low-confidence links over {state.pages} pages"
            )
            if NOISY_SECTIONS is not None:
                NOISY_SECTIONS.set(sum(1 for s in self._sections.values() if s.noisy))

    def stats(self) -> dict[str, Any]:
        return {
            "noisy_sections": sorted(k for k, v in self._sections.items() if v.noisy),
            "sections_tracked": len(self._sections),
            "skipped": self.skipped,
        }
