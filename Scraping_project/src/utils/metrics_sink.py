"""Never-silent, never-blocking metrics sink (#586).

Stage workers used to call ``PostgresManager.log_performance_metric`` /
``log_error`` inside ``try: ... except: logger.debug(...)``. When Postgres
was down, every metric and error record was dropped with nothing but a
DEBUG line, so operators were blind exactly when they needed data.

Dual-export path:

1. **Prometheus, always.** Every record updates the Prometheus series below,
   whether or not Postgres is configured or healthy. Prometheus is the
   source of truth for alerting and keeps working during a Postgres outage.
2. **Postgres, best effort.** The record is then written to
   ``performance_metrics`` / ``error_logs`` for history and ad-hoc SQL.
3. **On a Postgres failure:** ``scrapy_pg_metrics_writes_total{outcome="failure"}``
   is incremented (alert ``PostgresMetricsSinkFailing``), and the record is
   emitted as a WARNING log line (``metrics_sink_fallback ...`` with a JSON
   payload), so nothing is discarded silently.

The helpers never raise and never retry inline, so the crawl is never blocked
by the metrics path.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

MAX_FALLBACK_MESSAGE_CHARS = 500
MAX_LABEL_CHARS = 64

try:
    from prometheus_client import Counter

    PG_METRICS_WRITES = Counter(
        "scrapy_pg_metrics_writes_total",
        "Metric/error records offered to the Postgres sink, by outcome (success|failure|disabled)",
        ["kind", "outcome"],
    )
    STAGE_URLS_PROCESSED = Counter(
        "scrapy_stage_urls_processed_total",
        "URLs processed per stage (dual-exported; independent of the Postgres sink)",
        ["stage"],
    )
    STAGE_BATCH_SECONDS = Counter(
        "scrapy_stage_batch_seconds_total",
        "Wall-clock seconds spent processing batches per stage (dual-exported)",
        ["stage"],
    )
    STAGE_ERRORS = Counter(
        "scrapy_stage_errors_total",
        "Per-URL processing errors per stage and error type (dual-exported)",
        ["stage", "error_type"],
    )
except Exception:  # prometheus_client missing or metric already registered
    PG_METRICS_WRITES = None
    STAGE_URLS_PROCESSED = None
    STAGE_BATCH_SECONDS = None
    STAGE_ERRORS = None


def _label(value: Any) -> str:
    text = str(value) if value is not None else "unknown"
    return text[:MAX_LABEL_CHARS] or "unknown"


def _count_write(kind: str, outcome: str) -> None:
    if PG_METRICS_WRITES is not None:
        PG_METRICS_WRITES.labels(kind=kind, outcome=outcome).inc()


def _fallback(kind: str, payload: dict[str, Any], exc: BaseException) -> None:
    logger.warning(
        "metrics_sink_fallback kind=%s error=%s payload=%s",
        kind,
        f"{type(exc).__name__}: {exc}"[:MAX_FALLBACK_MESSAGE_CHARS],
        json.dumps(payload, default=str, sort_keys=True),
    )


def record_performance(
    postgres: Any,
    stage: str,
    urls_processed: int,
    processing_time_seconds: float,
    worker_count: Optional[int] = None,
    memory_usage_mb: Optional[float] = None,
) -> bool:
    """Export a batch's throughput. Returns True if Postgres stored it."""
    try:
        if STAGE_URLS_PROCESSED is not None:
            STAGE_URLS_PROCESSED.labels(stage=_label(stage)).inc(max(0, int(urls_processed)))
        if STAGE_BATCH_SECONDS is not None:
            STAGE_BATCH_SECONDS.labels(stage=_label(stage)).inc(max(0.0, float(processing_time_seconds)))
    except Exception as exc:  # metrics must never break the crawl
        logger.debug("prometheus export failed: %s", exc)

    if postgres is None:
        _count_write("performance", "disabled")
        return False
    try:
        postgres.log_performance_metric(
            stage=stage,
            urls_processed=urls_processed,
            processing_time_seconds=processing_time_seconds,
            worker_count=worker_count,
            memory_usage_mb=memory_usage_mb,
        )
    except Exception as exc:
        _count_write("performance", "failure")
        _fallback("performance", {
            "stage": stage,
            "urls_processed": urls_processed,
            "processing_time_seconds": processing_time_seconds,
            "worker_count": worker_count,
            "memory_usage_mb": memory_usage_mb,
        }, exc)
        return False
    _count_write("performance", "success")
    return True


def record_error(
    postgres: Any,
    stage: str,
    url: str,
    error_type: str,
    error_message: Optional[str] = None,
    http_status_code: Optional[int] = None,
    stack_trace: Optional[str] = None,
    retry_count: int = 0,
) -> bool:
    """Export a per-URL processing error. Returns True if Postgres stored it."""
    try:
        if STAGE_ERRORS is not None:
            STAGE_ERRORS.labels(stage=_label(stage), error_type=_label(error_type)).inc()
    except Exception as exc:
        logger.debug("prometheus export failed: %s", exc)

    if postgres is None:
        _count_write("error", "disabled")
        return False
    try:
        postgres.log_error(
            stage=stage,
            url=url,
            error_type=error_type,
            error_message=error_message,
            stack_trace=stack_trace,
            http_status_code=http_status_code,
            retry_count=retry_count,
        )
    except Exception as exc:
        _count_write("error", "failure")
        _fallback("error", {
            "stage": stage,
            "url": url,
            "error_type": error_type,
            "error_message": (error_message or "")[:MAX_FALLBACK_MESSAGE_CHARS],
            "http_status_code": http_status_code,
            "retry_count": retry_count,
        }, exc)
        return False
    _count_write("error", "success")
    return True
