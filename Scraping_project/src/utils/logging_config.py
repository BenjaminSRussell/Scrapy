"""Process-wide logging setup with correlation fields (#466) and a JSON mode (#238).

    from src.utils.logging_config import configure_logging, log_context

    configure_logging("stage2")                 # once, at process start
    with log_context(url=url, url_hash=h):      # per unit of work
        logger.info("fetched")                  # carries stage/worker_id/crawl_job_id/url

Every record gets ``stage``, ``worker_id`` and ``crawl_job_id`` attributes, plus whatever
the innermost ``log_context`` set. ``crawl_job_id`` resolves in this order: ``log_context``,
then the ``configure_logging(crawl_job_id=...)`` argument, then the OpenTelemetry
correlation id (``src.otel_tracing``), then ``$CRAWL_JOB_ID``.

``LOG_FORMAT=text`` (default) keeps the familiar human line and appends a compact
``[stage=... worker=... job=...]`` tag. ``LOG_FORMAT=json`` emits one JSON object per line
for Loki/OTel pipelines. ``LOG_LEVEL`` sets the root level (default INFO). Credential
redaction (``src.log_redaction``, #680) runs before formatting, so both modes are redacted.

Before this module the Stage 2/3/4 entrypoints (``python -m src.workers.stageN_worker``,
which is what docker-compose runs) never configured logging. The root logger had no
handler, so every INFO line from those workers was silently dropped.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, TextIO

TEXT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s%(correlation)s"
TEXT_DATEFMT = "%Y-%m-%d %H:%M:%S"
CORRELATION_FIELDS = ("stage", "worker_id", "crawl_job_id")
_TEXT_TAGS = (("stage", "stage"), ("worker_id", "worker"), ("crawl_job_id", "job"), ("url_hash", "url_hash"))

_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("log_context", default={})
_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "correlation"}
_HANDLER_MARK = "_scrapy_logging_config"


def default_worker_id() -> str:
    return os.getenv("WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}"


def _otel_job_id() -> str | None:
    try:
        from src.otel_tracing import get_crawl_job_id

        return get_crawl_job_id()
    except Exception:
        return None


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Attach fields (e.g. ``url``, ``url_hash``, ``crawl_job_id``) to every record in this block."""
    merged = {**_CONTEXT.get(), **{k: v for k, v in fields.items() if v is not None}}
    token = _CONTEXT.set(merged)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


class CorrelationFilter(logging.Filter):
    """Adds stage / worker_id / crawl_job_id / log_context fields to each record. Never drops records."""

    def __init__(self, stage: str, worker_id: str | None = None, crawl_job_id: str | None = None):
        super().__init__()
        self.stage = stage
        self.worker_id = worker_id or default_worker_id()
        self.crawl_job_id = crawl_job_id

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = _CONTEXT.get()
        for key, value in ctx.items():
            if not hasattr(record, key) or key in CORRELATION_FIELDS:
                setattr(record, key, value)
        if getattr(record, "stage", None) is None:
            record.stage = self.stage
        if getattr(record, "worker_id", None) is None:
            record.worker_id = self.worker_id
        if getattr(record, "crawl_job_id", None) is None:
            record.crawl_job_id = self.crawl_job_id or _otel_job_id() or os.getenv("CRAWL_JOB_ID") or None
        tags = [f"{label}={getattr(record, attr)}" for attr, label in _TEXT_TAGS if getattr(record, attr, None)]
        record.correlation = f" [{' '.join(tags)}]" if tags else ""
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, message, correlation fields, extras, exc_info."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key.startswith("_") or value is None:
                continue
            payload[key] = value if isinstance(value, (str, int, float, bool)) else repr(value)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        elif record.exc_text:
            payload["exc_info"] = record.exc_text
        if record.stack_info:
            payload["stack_info"] = record.stack_info
        return json.dumps(payload, ensure_ascii=False, default=str)


def _formatter(fmt: str) -> logging.Formatter:
    if fmt == "json":
        return JsonFormatter()
    return logging.Formatter(TEXT_FORMAT, datefmt=TEXT_DATEFMT)


def configure_logging(
    stage: str,
    worker_id: str | None = None,
    crawl_job_id: str | None = None,
    *,
    level: str | int | None = None,
    fmt: str | None = None,
    stream: TextIO | None = None,
) -> logging.Handler:
    """Install (or update) this process's root handler. Safe to call more than once.

    Without existing root handlers this adds one stream handler, like ``basicConfig``.
    If something else already installed root handlers (Scrapy, pytest), the correlation
    filter is attached to them as well. Only our own handler is ever replaced.
    """
    fmt = (fmt or os.getenv("LOG_FORMAT") or "text").strip().lower()
    if fmt not in ("text", "json"):
        raise ValueError(f"LOG_FORMAT must be 'text' or 'json', got {fmt!r}")
    level = level if level is not None else (os.getenv("LOG_LEVEL") or "INFO")
    root = logging.getLogger()
    root.setLevel(level if isinstance(level, int) else str(level).upper())

    corr = CorrelationFilter(stage, worker_id, crawl_job_id)
    ours = next((h for h in root.handlers if getattr(h, _HANDLER_MARK, False)), None)
    if ours is not None:
        root.removeHandler(ours)
    for h in root.handlers:  # foreign handlers: keep them, just add the fields
        for f in [f for f in h.filters if isinstance(f, CorrelationFilter)]:
            h.removeFilter(f)
        h.addFilter(corr)

    handler = logging.StreamHandler(stream or sys.stderr)
    setattr(handler, _HANDLER_MARK, True)
    handler.addFilter(corr)
    handler.setFormatter(_formatter(fmt))
    # Add ours when nothing else is installed (basicConfig semantics), when JSON was asked for
    # (foreign text handlers can't produce it), when an explicit stream was given, or to replace
    # our previous handler.
    if not root.handlers or fmt == "json" or stream is not None or ours is not None:
        root.addHandler(handler)
    return handler
