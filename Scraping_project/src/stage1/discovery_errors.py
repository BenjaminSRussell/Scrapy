"""Shared discovery error codes for rustmapper and Stage 1 (#933).

Rust-sitemap (`sitemap_seeder` / `network`) is not in this repo. Both sides
still share one handoff field, ``error_code``, and this retry table. Stage 1
classifies its own sitemap failures into the same codes an orchestrator uses
to route retries. String messages stay on ``error`` for humans.

Canonical values (any case on read):

* ``OK`` — walk finished with URLs and no coded failure
* ``PARTIAL`` — some URLs, plus a cap or a coded failure on another document
* ``TIMEOUT`` — deadline or a dropped connection (queue retry)
* ``ROBOTS_DENY`` — robots disallow (do not retry)
* ``HTTP_429`` — rate limited (queue retry, not in-request; #582)
* ``PARSE_FAIL`` — sitemap or JSONL could not be parsed (do not retry)

HTTP 404 on a sitemap candidate is not a code. No successful document and no
coded failure stays ``empty``, which is not retried as a failure.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from enum import StrEnum
from typing import Protocol, cast

logger = logging.getLogger(__name__)

_STATUS_429 = re.compile(r"(?<!\d)429(?!\d)")


class DiscoveryErrorCode(StrEnum):
    """Handoff ``error_code`` shared by rustmapper JSONL/sidecars and Stage 1."""

    TIMEOUT = "TIMEOUT"
    ROBOTS_DENY = "ROBOTS_DENY"
    HTTP_429 = "HTTP_429"
    PARSE_FAIL = "PARSE_FAIL"
    PARTIAL = "PARTIAL"
    OK = "OK"


class DiscoveryRetryAction(StrEnum):
    """What Stage 1 tells the orchestrator to do with a code."""

    NONE = "none"
    INGEST_PARTIAL = "ingest_partial"
    QUEUE = "queue"
    DROP = "drop"


# Every code has one row. Queue rows use RetryPolicy from src.utils.retry
# (queue_attempts, backoff_base, backoff_max, retryable_statuses).
RETRY_POLICY_TABLE: dict[DiscoveryErrorCode, DiscoveryRetryAction] = {
    DiscoveryErrorCode.OK: DiscoveryRetryAction.NONE,
    DiscoveryErrorCode.PARTIAL: DiscoveryRetryAction.INGEST_PARTIAL,
    DiscoveryErrorCode.TIMEOUT: DiscoveryRetryAction.QUEUE,
    DiscoveryErrorCode.HTTP_429: DiscoveryRetryAction.QUEUE,
    DiscoveryErrorCode.ROBOTS_DENY: DiscoveryRetryAction.DROP,
    DiscoveryErrorCode.PARSE_FAIL: DiscoveryRetryAction.DROP,
}

_FAILURE_ORDER = (
    DiscoveryErrorCode.ROBOTS_DENY,
    DiscoveryErrorCode.HTTP_429,
    DiscoveryErrorCode.TIMEOUT,
    DiscoveryErrorCode.PARSE_FAIL,
)

_RETRY_ACTIONS = frozenset({DiscoveryRetryAction.QUEUE})

_TIMEOUT_NAMES = frozenset(
    {
        "TimeoutException",
        "TimeoutError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "ConnectError",
        "ConnectTimeoutError",
        "ReadError",
        "WriteError",
        "NetworkError",
    }
)
_PARSE_NAMES = frozenset(
    {
        "ParseError",
        "DefusedXmlException",
        "JSONDecodeError",
        "BadGzipFile",
        "XMLSyntaxError",
    }
)
_ROBOTS_NEEDLES = (
    "robots.txt",
    "robots_deny",
    "robotstxt",
    "denied by robots",
    "robots deny",
    "robots disallow",
)
_PARSE_NEEDLES = (
    "failed to parse",
    "parse error",
    "parse failed",
    "not well-formed",
    "decompress",
    "bad gzip",
    "invalid xml",
    "json decode",
)
_TIMEOUT_NEEDLES = (
    "timed out",
    "timeout",
    "connection refused",
    "connection reset",
    "connect error",
)


class RetryPolicyView(Protocol):
    """Slice of ``src.utils.retry.RetryPolicy`` this table reads."""

    queue_attempts: int
    backoff_base: float
    backoff_max: float
    retryable_statuses: frozenset[int]

    def in_request_statuses(self) -> frozenset[int]: ...


def coerce_error_code(value: DiscoveryErrorCode | str | None) -> DiscoveryErrorCode | None:
    """Return a known code, or None when the value is missing or not in the taxonomy."""
    if value is None or value == "":
        return None
    if isinstance(value, DiscoveryErrorCode):
        return value
    try:
        return DiscoveryErrorCode(str(value).strip().upper())
    except ValueError:
        logger.warning("ignoring unknown discovery error_code %r", value)
        return None


def classify_discovery_failure(
    message: str | None = None,
    exc: BaseException | None = None,
) -> DiscoveryErrorCode | None:
    """Map a Stage 1 or rustmapper failure onto one code, or None if it is not one of them."""
    status = _status_code(exc)
    text = f"{type(exc).__name__ if exc else ''} {message or ''} {exc or ''}".lower()
    if status == 429 or _STATUS_429.search(text):
        return DiscoveryErrorCode.HTTP_429
    robots = any(needle in text for needle in _ROBOTS_NEEDLES)
    timed_out = _is_timeout(exc, text)
    # A robots fetch that timed out is retried. A disallow is not.
    if robots and not timed_out:
        return DiscoveryErrorCode.ROBOTS_DENY
    if timed_out:
        return DiscoveryErrorCode.TIMEOUT
    if _is_parse(exc, text):
        return DiscoveryErrorCode.PARSE_FAIL
    return None


def dominant_code(codes: Iterable[DiscoveryErrorCode | str | None]) -> DiscoveryErrorCode | None:
    """Pick one failure code. Robots wins, then 429, then timeout, then parse.

    A robots deny must not be retried just because another document also timed out.
    """
    present: set[DiscoveryErrorCode] = set()
    for code in codes:
        coerced = coerce_error_code(code)
        if coerced in _FAILURE_ORDER:
            present.add(coerced)
    for code in _FAILURE_ORDER:
        if code in present:
            return code
    return None


def select_handoff_code(
    *,
    urls_found: bool,
    codes: Iterable[DiscoveryErrorCode | str | None] = (),
    limits_hit: bool = False,
    timed_out: bool = False,
    fatal: BaseException | str | None = None,
) -> DiscoveryErrorCode | None:
    """Code for a finished walk. None means empty (no URLs and no coded failure)."""
    failure_codes: list[DiscoveryErrorCode | str | None] = list(codes)
    if timed_out:
        failure_codes.append(DiscoveryErrorCode.TIMEOUT)
    if isinstance(fatal, BaseException):
        failure_codes.append(classify_discovery_failure(str(fatal), exc=fatal))
    elif isinstance(fatal, str):
        failure_codes.append(classify_discovery_failure(fatal))
    if timed_out or fatal is not None:
        return dominant_code(failure_codes)
    if urls_found:
        if limits_hit or dominant_code(failure_codes) is not None:
            return DiscoveryErrorCode.PARTIAL
        return DiscoveryErrorCode.OK
    return dominant_code(failure_codes)


def stage1_retry_plan(
    code: DiscoveryErrorCode | str | None,
    policy: RetryPolicyView | None = None,
    *,
    load: bool = True,
) -> dict[str, object]:
    """Map a handoff code onto the shared retry table (#933, #215, #582).

    ``load=False`` skips ``load_retry_policy`` (no config read). Pass ``policy``
    to fill queue attempt and backoff fields for ``TIMEOUT`` and ``HTTP_429``.
    """
    coerced = coerce_error_code(code)
    if coerced is None:
        return {
            "code": None,
            "action": "unclassified",
            "retry": False,
            "policy_ref": "no retry; string did not match the taxonomy",
        }
    action = RETRY_POLICY_TABLE[coerced]
    plan: dict[str, object] = {
        "code": coerced.value,
        "action": action.value,
        "retry": action in _RETRY_ACTIONS,
        "policy_ref": _policy_ref(coerced, action),
    }
    if action is not DiscoveryRetryAction.QUEUE:
        return plan
    resolved = policy
    if resolved is None and load:
        resolved = _load_policy()
    if resolved is None:
        return plan
    in_request = _in_request(coerced, resolved)
    plan.update(
        {
            "in_request": in_request,
            "queue_attempts": resolved.queue_attempts,
            "backoff_base": resolved.backoff_base,
            "backoff_max": resolved.backoff_max,
        }
    )
    return plan


def _policy_ref(code: DiscoveryErrorCode, action: DiscoveryRetryAction) -> str:
    if code is DiscoveryErrorCode.HTTP_429:
        return (
            "RetryPolicy.queue_attempts, backoff_base, backoff_max; "
            "429 stays in retryable_statuses and out of in_request_statuses"
        )
    if code is DiscoveryErrorCode.TIMEOUT:
        return (
            "RetryPolicy.queue_attempts, backoff_base, backoff_max; "
            "in-request when 408 or 504 is in in_request_statuses"
        )
    if action is DiscoveryRetryAction.INGEST_PARTIAL:
        return "ingest discovered URLs; do not requeue the discovery job"
    return "no retry"


def _in_request(code: DiscoveryErrorCode, policy: RetryPolicyView) -> bool:
    if code is DiscoveryErrorCode.HTTP_429:
        return False
    statuses = policy.in_request_statuses()
    return bool(statuses & {408, 504})


def _load_policy() -> RetryPolicyView | None:
    try:
        from src.utils.retry import load_retry_policy
    except Exception as exc:
        logger.warning("discovery retry plan: retry policy import failed: %s", exc)
        return None
    try:
        return cast(RetryPolicyView, load_retry_policy())
    except Exception as exc:
        logger.warning("discovery retry plan: using the table without attempt counts: %s", exc)
        return None


def _status_code(exc: BaseException | None) -> int | None:
    if exc is None:
        return None
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _is_timeout(exc: BaseException | None, text: str) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if exc is not None and type(exc).__name__ in _TIMEOUT_NAMES:
        return True
    return any(needle in text for needle in _TIMEOUT_NEEDLES)


def _is_parse(exc: BaseException | None, text: str) -> bool:
    if exc is not None and type(exc).__name__ in _PARSE_NAMES:
        return True
    return any(needle in text for needle in _PARSE_NEEDLES)
