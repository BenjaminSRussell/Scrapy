"""Label contract for pipeline metrics (#243).

A Prometheus metric family has one set of label names. Grafana
``sum by (stage)`` (and the same for ``worker_id`` / ``domain``) drops or
splits series when one process emits the family with those labels and
another process emits it without them. The tuples below are the only
label sets. Both the Scrapy registry and the StatsD exporters use them.

Base labels on every crawl series and every shared StatsD family:

- ``stage`` — ``PIPELINE_STAGE`` when set, otherwise the caller's stage
  (``stage1``–``stage4``) or ``exporter`` for the metrics process. A value
  that is not ``[a-z][a-z0-9_]{0,31}`` is ``rejected``.
- ``worker_id`` — ``WORKER_ID``, else ``<hostname>-<pid>``. Same character
  pattern as a site id. A URL or anything else is ``rejected``.
- ``domain`` — ``METRIC_DOMAIN``, else ``unknown``. Same pattern. Not a
  request host. Callers cannot pass a domain.

``site_id`` is unchanged (#192): ``metric_site_id()`` is ``SITE_ID``, else
``TENANT_ID``, else ``REDIS_SITE_ID``, else ``default``. Helpers here do not
take a site id, URL, job id, or host. A value that fails the site-id pattern
is ``rejected``. The series is still recorded. There is no numeric site cap.

``pipeline_estimated_cost_usd`` (#545) is a FinOps proxy, not a cloud bill.
Its labels are the base set plus ``site_id`` and ``crawl_job_id``. The job
label is the spider attribute, else the OpenTelemetry context or
``CRAWL_JOB_ID``, else ``unknown``. The same pattern check applies: a bad
value is ``rejected`` and the series is still recorded. ``site_id`` is never
that job id.

Delta Lake manager lifecycle metrics stay unlabeled in every process. They
are not part of ``SCRAPY_FAMILIES``. Circuit-breaker and Delta table gauges
keep the label sets in ``STATSD_FAMILIES`` and do not gain the base set.
"""

from __future__ import annotations

import os
import re
import socket
from typing import Any

# Same pattern resolve_site_id uses (#191 / #192). Not imported from redis at
# module level so a metrics helper does not pull the Redis client in first.
_SITE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_STAGE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
REJECTED = "rejected"

BASE_LABELS = ("stage", "worker_id", "domain")
_IDENTITY = ("stage", "worker_id", "domain", "site_id")

# prometheus_client strips a trailing ``_total`` from Counter._name.
SCRAPY_FAMILIES: dict[str, tuple[str, ...]] = {
    "scrapy_items_scraped": BASE_LABELS + ("site_id", "spider"),
    "scrapy_items_dropped": BASE_LABELS + ("site_id", "spider"),
    "scrapy_requests": BASE_LABELS + ("site_id", "spider", "method"),
    "scrapy_responses": BASE_LABELS + ("site_id", "spider", "status_code"),
    "scrapy_response_time_seconds": BASE_LABELS + ("spider",),
    "scrapy_spider_opened": BASE_LABELS + ("spider",),
    "scrapy_spider_closed": BASE_LABELS + ("spider", "reason"),
    "scrapy_spider_errors": BASE_LABELS + ("site_id", "spider", "exception_type"),
    "scrapy_requests_dropped": BASE_LABELS + ("spider", "reason"),
    "scrapy_downloader_request_bytes": BASE_LABELS + ("spider",),
    "scrapy_downloader_response_bytes": BASE_LABELS + ("spider",),
    "scrapy_crawl_duration_seconds": BASE_LABELS + ("spider",),
    "scrapy_urls_skipped": BASE_LABELS + ("spider", "skip_reason"),
    "scrapy_new_urls_found_per_minute": BASE_LABELS + ("site_id", "spider"),
    "scrapy_average_file_size_bytes": BASE_LABELS + ("spider",),
    "scrapy_offsite_links_found": BASE_LABELS + ("spider",),
    "scrapy_offsite_candidates_saved": BASE_LABELS + ("spider",),
    "scrapy_crawler_content_summary": BASE_LABELS + ("spider",),
    "scrapy_crawler_summary_skipped": BASE_LABELS + ("spider", "reason"),
    "scrapy_hidden_urls_found": BASE_LABELS + ("spider", "discovery_method"),
    "scrapy_hidden_urls_routed": BASE_LABELS + ("spider", "route"),
    # Request count times COST_USD_PER_REQUEST. Not a measured cloud bill.
    "pipeline_estimated_cost_usd": BASE_LABELS + ("site_id", "crawl_job_id"),
}

# #192 series. site_id stays on these and is not a substitute for domain.
# The FinOps proxy keeps site_id as well (#545).
SITE_ID_FAMILIES = frozenset(
    {
        "scrapy_items_scraped",
        "scrapy_items_dropped",
        "scrapy_requests",
        "scrapy_responses",
        "scrapy_spider_errors",
        "scrapy_new_urls_found_per_minute",
        "pipeline_estimated_cost_usd",
    }
)

_BASE_AND_SITE = BASE_LABELS + ("site_id",)

# StatsD names. One tuple per family, shared by metrics_exporter and MetricsManager.
STATSD_FAMILIES: dict[str, tuple[str, ...]] = {
    "redis.queue.length": _BASE_AND_SITE + ("queue",),
    "urls.processed.total": _BASE_AND_SITE,
    "urls.processed.per_second": _BASE_AND_SITE,
    "urls.discovered.total": _BASE_AND_SITE,
    "errors.total": _BASE_AND_SITE + ("error_type",),
    "workers.active": _BASE_AND_SITE,
    "summaries.created.total": _BASE_AND_SITE,
    "stage4.http.requests.total": _BASE_AND_SITE,
    "stage4.http.failures.total": _BASE_AND_SITE + ("error_type",),
    "circuit_breaker.open_count": (),
    "circuit_breaker.state": ("name", "host"),
    "delta_lake.records": ("table",),
    "delta_lake.size_bytes": ("table",),
    "delta_lake.total_records": (),
    "urls.seed.total": (),
    "urls.uconn.total": (),
}

STAGE4_HTTP_REQUEST_LABELS = STATSD_FAMILIES["stage4.http.requests.total"]
STAGE4_HTTP_FAILURE_LABELS = STATSD_FAMILIES["stage4.http.failures.total"]

# Families both the exporter and in-process callers emit. Their key sets must match.
SHARED_STATSD_FAMILIES = ("urls.processed.total", "urls.discovered.total")


def bounded_site_token(value: str | None) -> str:
    """Site-id pattern, or ``rejected``. Does not drop the series."""
    text = str(value or "").strip()
    if _SITE_RE.fullmatch(text) is None:
        return REJECTED
    return text


def bound_stage(value: str | None, default: str = "unknown") -> str:
    """Short stage token. Empty uses ``default``. Anything else is ``rejected``."""
    text = str(value or "").strip().lower()
    if not text:
        text = str(default or "").strip().lower()
    if _STAGE_RE.fullmatch(text) is None:
        return REJECTED
    return text


def metric_stage(default: str = "stage1") -> str:
    """``PIPELINE_STAGE`` when set, otherwise ``default``. No caller-supplied URL."""
    raw = os.environ.get("PIPELINE_STAGE", "")
    if str(raw).strip():
        return bound_stage(raw, default=default)
    return bound_stage(default, default="unknown")


def metric_worker_id() -> str:
    """``WORKER_ID`` or ``<hostname>-<pid>``. Not a URL, job id, or free-form host."""
    raw = os.environ.get("WORKER_ID", "").strip()
    if not raw:
        raw = f"{socket.gethostname()}-{os.getpid()}"
    return bounded_site_token(raw)


def metric_domain() -> str:
    """``METRIC_DOMAIN`` or ``unknown``. Callers cannot pass a URL, job id, or host."""
    raw = os.environ.get("METRIC_DOMAIN", "").strip()
    if not raw:
        return "unknown"
    return bounded_site_token(raw)


def _metric_site_id() -> str:
    from src.utils.redis import metric_site_id

    return metric_site_id()


def metric_crawl_job_id(spider: Any = None) -> str:
    """Job label for the cost proxy (#545). Not used as ``site_id``.

    Spider ``crawl_job_id``, else the OpenTelemetry context / ``CRAWL_JOB_ID``.
    Empty is ``unknown``. A value that fails the site-id pattern is
    ``rejected``. The series is still recorded. This does not mint a new id.
    """
    raw = ""
    if spider is not None:
        candidate = getattr(spider, "crawl_job_id", None)
        if candidate is not None and str(candidate).strip():
            raw = str(candidate).strip()
    if not raw:
        try:
            from src.otel_tracing import get_crawl_job_id

            raw = str(get_crawl_job_id() or "").strip()
        except Exception:
            raw = os.environ.get("CRAWL_JOB_ID", "").strip()
    if not raw:
        return "unknown"
    return bounded_site_token(raw)


def cost_proxy_labels(spider: Any = None) -> dict[str, str]:
    """Labels for ``pipeline_estimated_cost_usd``.

    ``site_id`` comes only from ``metric_site_id()``. Callers cannot pass a
    URL, a job id, or a raw host. ``crawl_job_id`` is ``metric_crawl_job_id``.
    """
    labels = identity_labels()
    labels["crawl_job_id"] = metric_crawl_job_id(spider)
    return {name: labels[name] for name in SCRAPY_FAMILIES["pipeline_estimated_cost_usd"]}


def identity_labels(*, stage: str | None = None, default_stage: str = "stage1") -> dict[str, str]:
    """The four identity labels. ``stage=None`` reads ``PIPELINE_STAGE`` / ``default_stage``."""
    if stage is None:
        chosen = metric_stage(default=default_stage)
    else:
        chosen = bound_stage(stage, default=default_stage)
    return {
        "stage": chosen,
        "worker_id": metric_worker_id(),
        "domain": metric_domain(),
        "site_id": _metric_site_id(),
    }


def core_tags(
    extra: dict[str, Any] | None = None,
    *,
    default_stage: str = "stage1",
    site_id: str | None = None,
) -> dict[str, str]:
    """StatsD tags for one family.

    ``site_id``, ``worker_id``, and ``domain`` in ``extra`` are ignored so a
    caller cannot put a URL on the series. An explicit ``stage`` in ``extra``
    wins over ``PIPELINE_STAGE``. ``site_id`` is the configured id, or the
    exporter's already-resolved snapshot after the same pattern check.
    """
    cleaned: dict[str, Any] = dict(extra or {})
    stage_value = cleaned.pop("stage", None)
    for key in ("site_id", "worker_id", "domain"):
        cleaned.pop(key, None)
    labels = identity_labels(
        stage=None if stage_value is None else str(stage_value),
        default_stage=default_stage,
    )
    if site_id is not None:
        labels["site_id"] = bounded_site_token(site_id)
    for key, value in cleaned.items():
        labels[str(key)] = "" if value is None else str(value)
    return labels


def labels_for(metric: Any, *, stage: str | None = None, **extra: Any) -> Any:
    """Child for ``metric`` using its registered names.

    Identity labels are never taken from ``extra`` (a URL passed as
    ``site_id=`` or ``domain=`` is ignored). Other label names are required.
    """
    ident = identity_labels(stage=stage)
    values: dict[str, Any] = {}
    for name in metric._labelnames:
        if name in _IDENTITY:
            values[name] = ident[name]
        else:
            try:
                values[name] = extra[name]
            except KeyError as exc:
                raise TypeError(f"missing label {name} for {metric._name}") from exc
    return metric.labels(**values)


def sample_labels(family: str, **specific: Any) -> dict[str, str]:
    """Full label dict for ``REGISTRY.get_sample_value`` on a Scrapy family."""
    ident = identity_labels()
    labels: dict[str, str] = {}
    for name in SCRAPY_FAMILIES[family]:
        if name in _IDENTITY:
            labels[name] = ident[name]
        else:
            labels[name] = str(specific[name])
    return labels


def scrapy_family_errors(metrics: dict[str, tuple[str, ...]]) -> list[str]:
    """Differences between the live registry and ``SCRAPY_FAMILIES``.

    ``delta_manager_*`` must stay unlabeled. Every other family in the
    registry must be listed, and every listed family must be registered.
    """
    errors: list[str] = []
    for name, labels in sorted(metrics.items()):
        if name in SCRAPY_FAMILIES:
            expected = SCRAPY_FAMILIES[name]
            if tuple(labels) != expected:
                errors.append(f"{name}: {tuple(labels)} != {expected}")
            elif not set(BASE_LABELS) <= set(expected):
                errors.append(f"{name} is missing base labels {BASE_LABELS}")
            if name in SITE_ID_FAMILIES and "site_id" not in labels:
                errors.append(f"{name} dropped site_id")
        elif name.startswith("delta_manager"):
            if tuple(labels) != ():
                errors.append(f"{name} label set changed: {tuple(labels)}")
        else:
            errors.append(f"undocumented metric family {name}")
    for name in SCRAPY_FAMILIES:
        if name not in metrics:
            errors.append(f"{name} is not registered")
        elif name in SITE_ID_FAMILIES and "site_id" not in SCRAPY_FAMILIES[name]:
            errors.append(f"{name} contract dropped site_id")
    return errors
