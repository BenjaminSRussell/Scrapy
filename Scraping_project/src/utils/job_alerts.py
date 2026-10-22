"""Job failure and stall webhooks (#426).

A named job that fails or stalls posts one JSON payload to a webhook URL. The
URL is ``ALERT_WEBHOOK_URL`` when set, else ``alerts.webhook_url`` in
config.yml. Per-job fail/stall toggles and quiet hours come from job metadata
(#487), so a muted job posts nothing.

Sending never raises. A bad URL or a dead receiver is logged and reported as
``False``, so an alert cannot fail a run.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from src.utils.job_notice_prefs import allow_notice_from_metadata, local_hour_of
from src.utils.text_truncate import truncate_text

logger = logging.getLogger(__name__)

WEBHOOK_ENV = "ALERT_WEBHOOK_URL"
SECTION = "alerts"
DEFAULT_STALL_MINUTES = 30
ERROR_SNIPPET_LIMIT = 500
SEND_TIMEOUT = 5.0

_VERBS = {"fail": "failed", "stall": "stalled"}


def _valid_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname) and parts.username is None and parts.password is None


def webhook_url(env: Mapping[str, str] | None = None) -> str | None:
    """The configured webhook URL, or None when unset or not a plain http(s) URL."""
    source = os.environ if env is None else env
    url = (source.get(WEBHOOK_ENV) or "").strip()
    if not url:
        url = str(_config_section().get("webhook_url") or "").strip()
    if not url:
        return None
    if not _valid_url(url):
        logger.warning("Ignoring alert webhook URL: must be http(s) without credentials")
        return None
    return url


def stall_minutes() -> float:
    """Minutes without progress before a job counts as stalled. Zero or less turns stall alerts off."""
    raw = _config_section().get("stall_minutes", DEFAULT_STALL_MINUTES)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return DEFAULT_STALL_MINUTES


def _config_section() -> dict[str, Any]:
    try:
        from src.core.config import get_config

        return dict(get_config().get_section(SECTION) or {})
    except Exception:  # config unavailable (tests, partial installs): env only
        return {}


def is_stalled(last_progress: datetime, now: datetime, minutes: float) -> bool:
    """True when ``now`` is at least ``minutes`` past ``last_progress``. Both must share a timezone rule."""
    if minutes <= 0:
        return False
    return (now - last_progress).total_seconds() >= minutes * 60


def error_snippet(error: BaseException | str | None) -> str | None:
    """The last error as one short line, or None when there is none."""
    if error is None:
        return None
    text = " ".join(str(error).split())
    if not text:
        return None
    return truncate_text(text, ERROR_SNIPPET_LIMIT)


def build_payload(
    kind: str,
    job_name: str,
    *,
    stage: str,
    last_error: BaseException | str | None,
    when: datetime,
) -> dict[str, Any]:
    """Payload for a failed or stalled job. ``text`` lets Slack-style receivers show it as-is."""
    if kind not in _VERBS:
        raise ValueError(f"unknown alert kind: {kind}")
    snippet = error_snippet(last_error)
    text = f"Job {job_name} {_VERBS[kind]} at {stage}"
    if snippet:
        text += f": {snippet}"
    return {
        "event": f"job.{_VERBS[kind]}",
        "job": job_name,
        "stage": stage,
        "last_error": snippet,
        "sent_at": when.isoformat(),
        "text": text,
    }


def build_test_payload(when: datetime) -> dict[str, Any]:
    """Payload for the Control Center test button."""
    return {
        "event": "job.test",
        "job": "test-notification",
        "stage": "none",
        "last_error": None,
        "sent_at": when.isoformat(),
        "text": "Test notification from the Pipeline Control Center",
    }


def send_webhook(
    url: str,
    payload: Mapping[str, Any],
    *,
    timeout: float = SEND_TIMEOUT,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[bool, str]:
    """POST ``payload`` as JSON. Returns (delivered, detail). Never raises."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with opener(request, timeout=timeout) as response:
            status = response.status
    except Exception as exc:  # network, DNS, TLS, HTTP error status: report, never raise
        return False, type(exc).__name__
    if 200 <= status < 300:
        return True, f"HTTP {status}"
    return False, f"HTTP {status}"


def send_test(env: Mapping[str, str] | None = None, *, when: datetime | None = None, opener=urllib.request.urlopen) -> tuple[bool, str]:
    """Send the test notification to the configured webhook."""
    url = webhook_url(env)
    if url is None:
        return False, "no webhook URL configured"
    return send_webhook(url, build_test_payload(when or datetime.now().astimezone()), opener=opener)


def notify_job(
    kind: str,
    job_name: str,
    *,
    stage: str,
    last_error: BaseException | str | None = None,
    metadata: Mapping[str, Any] | None = None,
    when: datetime | None = None,
    env: Mapping[str, str] | None = None,
    opener=urllib.request.urlopen,
) -> bool:
    """Post a fail or stall alert when a webhook is set and the job's prefs allow it. Returns True when delivered."""
    try:
        url = webhook_url(env)
        if url is None:
            return False
        now = when or datetime.now().astimezone()
        if not allow_notice_from_metadata(kind, metadata, local_hour_of(now)):
            return False
        payload = build_payload(kind, job_name, stage=stage, last_error=last_error, when=now)
        delivered, detail = send_webhook(url, payload, opener=opener)
    except Exception:  # bad prefs or payload: the run must go on
        logger.exception("Job alert for %s could not be sent", job_name)
        return False
    if delivered:
        logger.info("Job alert %s for %s delivered (%s)", kind, job_name, detail)
    else:
        logger.warning("Job alert %s for %s not delivered (%s)", kind, job_name, detail)
    return delivered
