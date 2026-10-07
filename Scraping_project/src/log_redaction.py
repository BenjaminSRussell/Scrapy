"""Redact credentials from every log record (#680).

Installed process-wide from ``src/__init__.py`` by wrapping ``Logger.makeRecord``,
so it covers every logger and every handler, including Scrapy's root handler and
the scattered ``logging.basicConfig`` calls. It covers:

* the formatted message (``msg % args``),
* exception and stack text (pre-rendered into ``exc_text``/``stack_info``),
* string ``extra=`` fields (structured logging).

Only the standard library is used, so importing ``src`` stays cheap.
"""

from __future__ import annotations

import logging
import os
import re
import traceback
from typing import Any

REDACTED = "[REDACTED]"

_SEP = r"""["']?\s*[:=]\s*[\[\(]?\s*(?-i:b)?["']?"""
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization / Proxy-Authorization headers: keep the scheme, drop the credential.
    (
        re.compile(rf"(?i)((?:proxy-)?authorization{_SEP})((?:bearer|basic|token|digest)\s+)?[^\s\"',;\]\)]+"),
        rf"\1\2{REDACTED}",
    ),
    # Cookie / Set-Cookie header values.
    (re.compile(rf"(?i)((?:set-)?cookie{_SEP})[^\"'\n\]\)]+"), rf"\1{REDACTED}"),
    # Credentials embedded in URLs/DSNs: scheme://user:password@host
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:)[^@\s/]+(@)"), rf"\1{REDACTED}\2"),
    # key=value / "key": "value" style secrets.
    (
        re.compile(
            rf"(?i)\b((?:[a-z0-9]+[_-])*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
            rf"private[_-]?key|session[_-]?id|sessionid|csrftoken){_SEP})([^\s\"',;&}}\]\)]+)"
        ),
        rf"\1{REDACTED}",
    ),
    # Bare bearer tokens anywhere in text.
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=\-]{8,}"), rf"\1{REDACTED}"),
]

_SECRET_ENV_NAME = re.compile(r"(?i)(password|passwd|secret|token|api_?key|access_?key|private_?key|dsn)")
_env_secrets: list[str] = []


def refresh_env_secrets() -> None:
    """Re-read secret-looking environment variables (values of 6+ chars)."""
    global _env_secrets
    values = {
        v for k, v in os.environ.items() if _SECRET_ENV_NAME.search(k) and isinstance(v, str) and len(v) >= 6
    }
    _env_secrets = sorted(values, key=len, reverse=True)


def redact(text: Any) -> Any:
    """Return ``text`` with credentials replaced by ``[REDACTED]`` (non-str passthrough)."""
    if not isinstance(text, str) or not text:
        return text
    for secret in _env_secrets:
        if secret in text:
            text = text.replace(secret, REDACTED)
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}
_installed = False


def _redact_record(record: logging.LogRecord) -> logging.LogRecord:
    try:
        message = record.getMessage()
    except Exception:
        message = str(record.msg)
    record.msg = redact(message)
    record.args = ()
    if record.exc_info and not record.exc_text:
        record.exc_text = "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
    if record.exc_text:
        record.exc_text = redact(record.exc_text)
    if record.stack_info:
        record.stack_info = redact(record.stack_info)
    for key, value in list(record.__dict__.items()):
        if key not in _STANDARD_ATTRS and isinstance(value, str):
            setattr(record, key, redact(value))
    return record


def install_log_redaction() -> None:
    """Wrap ``Logger.makeRecord`` once so every record is redacted at creation.

    ``makeRecord`` (rather than the LogRecord factory) is wrapped because it
    applies ``extra=`` fields after the factory runs, and those structured
    fields need redacting too.
    """
    global _installed
    if _installed:
        return
    refresh_env_secrets()
    original = logging.Logger.makeRecord

    def make_record(self: logging.Logger, *args: Any, **kwargs: Any) -> logging.LogRecord:
        return _redact_record(original(self, *args, **kwargs))

    make_record.__wrapped__ = original  # type: ignore[attr-defined]
    logging.Logger.makeRecord = make_record  # type: ignore[method-assign]
    _installed = True
