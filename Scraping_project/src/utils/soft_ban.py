"""Soft-ban / captcha detection and per-domain backoff (#582).

A 403/429/503 challenge page, or a 200 "verify you are human" interstitial,
must not be parsed as content: it poisons Stage 2/3 and burns crawl budget.

``SoftBanDetector.detect`` returns a signature name (str) for a soft-ban
response, or None. Rules:

* HTTP 429 is always a soft ban (``http_429``).
* ``cf-mitigated: challenge`` header is a Cloudflare challenge at any status.
* 403/503 count only when the body matches a signature (a plain 403 is a
  normal, terminal HTTP error).
* 200 counts only when a signature matches **and** the page is short
  (visible words < ``max_words``), so real articles that mention "captcha"
  are not flagged.

Signatures are configurable: ``SOFT_BAN_SIGNATURES`` (env) is a JSON object
``{"name": "regex", ...}`` merged over the defaults; an empty-string regex
disables a default signature.

``DomainBackoff`` trips a domain into cooldown when it produces
``threshold`` soft bans within ``window`` seconds; callers defer (not fail)
that domain's URLs until the cooldown ends.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import deque
from typing import Callable, Mapping, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

try:  # pragma: no cover - metrics optional
    from prometheus_client import Counter

    SOFT_BAN_TOTAL: Optional[Counter] = Counter(
        "scrapy_soft_ban_total",
        "Responses classified as soft-ban/captcha and quarantined, by stage and signature (#582).",
        ["stage", "signature"],
    )
    SOFT_BAN_BACKOFF_TRIPS: Optional[Counter] = Counter(
        "scrapy_soft_ban_domain_backoff_total",
        "Times a domain entered soft-ban cooldown (#582).",
        ["stage"],
    )
    SOFT_BAN_DEFERRED: Optional[Counter] = Counter(
        "scrapy_soft_ban_deferred_total",
        "Requests deferred because their domain is in soft-ban cooldown (#582).",
        ["stage"],
    )
except Exception:  # pragma: no cover
    SOFT_BAN_TOTAL = SOFT_BAN_BACKOFF_TRIPS = SOFT_BAN_DEFERRED = None

DEFAULT_SIGNATURES: dict[str, str] = {
    "cloudflare_challenge": (
        r"cf-chl-|/cdn-cgi/challenge-platform/|<title>\s*just a moment\.\.\.\s*</title>"
        r"|attention required!\s*\|\s*cloudflare|cf-browser-verification"
    ),
    "recaptcha": r"class=[\"']g-recaptcha|google\.com/recaptcha/api|recaptcha/api\.js",
    "hcaptcha": r"hcaptcha\.com/1/api\.js|class=[\"']h-captcha",
    "perimeterx": r"px-captcha|_pxcaptcha|perimeterx",
    "datadome": r"captcha-delivery\.com|datadome",
    "akamai_access_denied": r"<title>\s*access denied\s*</title>[\s\S]{0,2000}reference\s*#",
    "generic_bot_check": (
        r"are you a robot|unusual traffic from your (computer )?network"
        r"|verify (that )?you are (a )?human|please complete the security check"
        r"|enable javascript and cookies to continue"
    ),
}

CHALLENGE_STATUSES = frozenset({403, 503})
SCAN_BYTES = 65536
_TAG_RE = re.compile(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>", re.I)


def load_signatures(extra: Optional[Mapping[str, str]] = None) -> dict[str, re.Pattern[str]]:
    sigs: dict[str, str] = dict(DEFAULT_SIGNATURES)
    raw = os.getenv("SOFT_BAN_SIGNATURES", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("expected a JSON object")
            sigs.update({str(k): str(v) for k, v in parsed.items()})
        except ValueError as e:
            logger.error(f"[soft_ban] ignoring invalid SOFT_BAN_SIGNATURES: {e}")
    if extra:
        sigs.update(extra)
    compiled: dict[str, re.Pattern[str]] = {}
    for name, pattern in sigs.items():
        if not pattern:
            continue  # disabled
        try:
            compiled[name] = re.compile(pattern, re.I)
        except re.error as e:
            logger.error(f"[soft_ban] ignoring signature {name!r}: bad regex ({e})")
    return compiled


def _visible_words(html: str) -> int:
    return len(_TAG_RE.sub(" ", html).split())


class SoftBanDetector:
    def __init__(
        self,
        signatures: Optional[Mapping[str, str]] = None,
        max_words: Optional[int] = None,
    ):
        self.signatures = load_signatures(signatures)
        self.max_words = max_words if max_words is not None else int(os.getenv("SOFT_BAN_MAX_WORDS", "400"))

    def match_signature(self, body: str) -> Optional[str]:
        head = body[:SCAN_BYTES]
        for name, rx in self.signatures.items():
            if rx.search(head):
                return name
        return None

    def detect(
        self,
        status: int,
        body: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Optional[str]:
        if status == 429:
            return "http_429"
        if headers:
            lowered = {str(k).lower(): str(v).lower() for k, v in headers.items()}
            if lowered.get("cf-mitigated") == "challenge":
                return "cloudflare_challenge"
        if not body:
            return None
        if status in CHALLENGE_STATUSES:
            return self.match_signature(body)
        if 200 <= status < 300:
            sig = self.match_signature(body)
            if sig and _visible_words(body[:SCAN_BYTES]) < self.max_words:
                return sig
        return None


def domain_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


class DomainBackoff:
    """Thread-safe sliding-window soft-ban counter with per-domain cooldown."""

    def __init__(
        self,
        threshold: Optional[int] = None,
        window: Optional[float] = None,
        cooldown: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
        stage: str = "stage2",
    ):
        self.threshold = max(1, threshold if threshold is not None else int(os.getenv("SOFT_BAN_BACKOFF_THRESHOLD", "3")))
        self.window = window if window is not None else float(os.getenv("SOFT_BAN_BACKOFF_WINDOW", "60"))
        self.cooldown = cooldown if cooldown is not None else float(os.getenv("SOFT_BAN_BACKOFF_COOLDOWN", "300"))
        self.clock = clock
        self.stage = stage
        self._hits: dict[str, deque[float]] = {}
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def record(self, domain: str) -> bool:
        """Record a soft ban; True if this hit put the domain into cooldown."""
        if not domain:
            return False
        now = self.clock()
        with self._lock:
            hits = self._hits.setdefault(domain, deque())
            hits.append(now)
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.threshold and self._until.get(domain, 0.0) <= now:
                self._until[domain] = now + self.cooldown
                hits.clear()
                tripped = True
            else:
                tripped = False
        if tripped:
            logger.warning(
                f"[soft_ban] {domain}: {self.threshold} soft bans in {self.window:.0f}s; "
                f"backing off for {self.cooldown:.0f}s"
            )
            if SOFT_BAN_BACKOFF_TRIPS is not None:
                SOFT_BAN_BACKOFF_TRIPS.labels(stage=self.stage).inc()
        return tripped

    def blocked(self, domain: str) -> bool:
        if not domain:
            return False
        with self._lock:
            until = self._until.get(domain)
            if until is None:
                return False
            if until <= self.clock():
                del self._until[domain]
                return False
            return True

    def remaining(self, domain: str) -> float:
        with self._lock:
            return max(0.0, self._until.get(domain, 0.0) - self.clock())


def count_soft_ban(stage: str, signature: str) -> None:
    if SOFT_BAN_TOTAL is not None:
        SOFT_BAN_TOTAL.labels(stage=stage, signature=signature).inc()


def count_deferred(stage: str) -> None:
    if SOFT_BAN_DEFERRED is not None:
        SOFT_BAN_DEFERRED.labels(stage=stage).inc()
