"""Customer-success health score per org (#966).

The score blends product activity with finished-job success rate. It does not
read Redis, Postgres, Kafka, or the network. Callers pass job-history rows
(``org_id``, ``status``) and metering rows (``org_id``, ``pages_crawled``).
Other metering counters (JS renders, export GB) are ignored. There is no org
table on this tree; this module does not create one.

Activity is the higher of two 0–1 factors: pages crawled in the window over
``ACTIVITY_PAGES_FOR_FULL`` (100), and finished jobs over
``ACTIVITY_JOBS_FOR_FULL`` (4). Those denominators are blend scales, not plan
caps and not crawl SLOs. Success rate is succeeded / (succeeded + failed).
Running, pending, queued, and cancelled rows are not finished. The score is
``0.4 * activity + 0.6 * success_rate``. No finished jobs means no score
(idle or pages-only is not 0).

``publish_org_health`` sets ``org_customer_health_score{org_id}`` only for a
real score. ``OrgCustomerHealthLow`` warns below 0.5. That warning is not an
SLO. Stage2SuccessSLO, QueueBacklog (> 10000), and KafkaConsumerLagSLO
(> 250000) stay as they are.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.utils.metric_labels import REJECTED, bounded_site_token

ACTIVITY_WEIGHT = 0.4
SUCCESS_WEIGHT = 0.6
ACTIVITY_PAGES_FOR_FULL = 100.0
ACTIVITY_JOBS_FOR_FULL = 4
WARN_BELOW = 0.5

SUCCESS_STATUSES = frozenset({"succeeded", "success", "completed", "ok"})
FAILURE_STATUSES = frozenset({"failed", "failure", "error"})
IGNORED_STATUSES = frozenset({"running", "pending", "queued", "cancelled", "canceled"})

_ORG_ERROR = "org_id must be a site-id token"


@dataclass(frozen=True, slots=True)
class OrgHealth:
    """One org's score. ``score`` is None when success rate cannot be computed."""

    score: float | None
    activity: float
    success_rate: float | None
    reason: str
    pages_crawled: float
    succeeded_jobs: int
    failed_jobs: int

    @property
    def below_warn(self) -> bool:
        """True only for a numeric score strictly under ``WARN_BELOW``."""
        return self.score is not None and self.score < WARN_BELOW


try:
    from prometheus_client import Gauge

    ORG_CUSTOMER_HEALTH_SCORE: Any = Gauge(
        "org_customer_health_score",
        "Customer-success health per org, 0-1. Activity weight 0.4 plus success-rate weight 0.6 (#966). Not an SLO.",
        ["org_id"],
    )
    ORG_CUSTOMER_HEALTH_ACTIVITY: Any = Gauge(
        "org_customer_health_activity",
        "Activity factor for the org health score, 0-1 (#966).",
        ["org_id"],
    )
    ORG_CUSTOMER_HEALTH_SUCCESS_RATIO: Any = Gauge(
        "org_customer_health_success_ratio",
        "Finished-job success rate for the org health score, 0-1 (#966).",
        ["org_id"],
    )
except Exception:  # prometheus_client missing or metric already registered
    ORG_CUSTOMER_HEALTH_SCORE = None
    ORG_CUSTOMER_HEALTH_ACTIVITY = None
    ORG_CUSTOMER_HEALTH_SUCCESS_RATIO = None


def bound_org_id(value: object) -> str:
    """Site-id token used as the org label. Not a URL, email, or job id."""
    if not isinstance(value, str):
        raise ValueError(_ORG_ERROR)
    token = bounded_site_token(value)
    if token == REJECTED and value.strip() != REJECTED:
        raise ValueError(_ORG_ERROR)
    return token


def _as_count(value: object, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value


def _as_pages(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("pages_crawled must be a non-negative number")
    number = float(value)
    if number != number or number == float("inf") or number == float("-inf") or number < 0:
        raise ValueError("pages_crawled must be a non-negative number")
    return number


def activity_factor(pages_crawled: float, finished_jobs: int) -> float:
    """0–1 activity from metering pages and finished job-history rows."""
    pages = _as_pages(pages_crawled)
    finished = _as_count(finished_jobs, "finished_jobs")
    page_part = min(1.0, pages / ACTIVITY_PAGES_FOR_FULL)
    job_part = min(1.0, finished / ACTIVITY_JOBS_FOR_FULL)
    return max(page_part, job_part)


def score_org(*, pages_crawled: float, succeeded_jobs: int, failed_jobs: int) -> OrgHealth:
    """Blend activity and success rate. No finished jobs yields ``score=None``."""
    pages = _as_pages(pages_crawled)
    succeeded = _as_count(succeeded_jobs, "succeeded_jobs")
    failed = _as_count(failed_jobs, "failed_jobs")
    finished = succeeded + failed
    activity = activity_factor(pages, finished)
    if finished == 0:
        reason = "no_activity" if pages == 0 else "no_finished_jobs"
        return OrgHealth(
            score=None,
            activity=activity,
            success_rate=None,
            reason=reason,
            pages_crawled=pages,
            succeeded_jobs=0,
            failed_jobs=0,
        )
    success = succeeded / finished
    score = min(1.0, max(0.0, ACTIVITY_WEIGHT * activity + SUCCESS_WEIGHT * success))
    return OrgHealth(
        score=score,
        activity=activity,
        success_rate=success,
        reason="ok",
        pages_crawled=pages,
        succeeded_jobs=succeeded,
        failed_jobs=failed,
    )


def _classify(status: object) -> tuple[int, int]:
    if not isinstance(status, str):
        raise ValueError("job status must be a string")
    text = status.strip().lower()
    if text in SUCCESS_STATUSES:
        return 1, 0
    if text in FAILURE_STATUSES:
        return 0, 1
    if text in IGNORED_STATUSES:
        return 0, 0
    raise ValueError(f"unknown job status {text!r}")


def score_from_records(
    job_rows: Sequence[Mapping[str, Any]],
    meter_rows: Sequence[Mapping[str, Any]],
) -> dict[str, OrgHealth]:
    """Score each org present in job history, metering, or both.

    Meter rows for one org are summed (window deltas, not lifetime totals).
    A meter row must include ``pages_crawled``.
    """
    succeeded: dict[str, int] = {}
    failed: dict[str, int] = {}
    pages: dict[str, float] = {}
    for row in job_rows:
        if not isinstance(row, Mapping):
            raise ValueError("job history row must be a mapping")
        org = bound_org_id(row.get("org_id"))
        ok, bad = _classify(row.get("status"))
        succeeded[org] = succeeded.get(org, 0) + ok
        failed[org] = failed.get(org, 0) + bad
    for row in meter_rows:
        if not isinstance(row, Mapping):
            raise ValueError("meter row must be a mapping")
        if "pages_crawled" not in row:
            raise ValueError("meter row requires pages_crawled")
        org = bound_org_id(row.get("org_id"))
        pages[org] = pages.get(org, 0.0) + _as_pages(row.get("pages_crawled"))
    scored: dict[str, OrgHealth] = {}
    for org in sorted(set(succeeded) | set(pages)):
        scored[org] = score_org(
            pages_crawled=pages.get(org, 0.0),
            succeeded_jobs=succeeded.get(org, 0),
            failed_jobs=failed.get(org, 0),
        )
    return scored


def clear_org_health_gauges() -> None:
    """Drop every org series. Tests and a fresh publish call this."""
    for gauge in (
        ORG_CUSTOMER_HEALTH_SCORE,
        ORG_CUSTOMER_HEALTH_ACTIVITY,
        ORG_CUSTOMER_HEALTH_SUCCESS_RATIO,
    ):
        if gauge is not None:
            gauge.clear()


def publish_org_health(scores: Mapping[str, OrgHealth]) -> None:
    """Replace gauge samples. A missing score removes that org instead of writing 0."""
    if ORG_CUSTOMER_HEALTH_SCORE is None:
        return
    clear_org_health_gauges()
    for org_id, health in scores.items():
        token = bound_org_id(org_id)
        if health.score is None or health.success_rate is None:
            continue
        ORG_CUSTOMER_HEALTH_SCORE.labels(org_id=token).set(health.score)
        ORG_CUSTOMER_HEALTH_ACTIVITY.labels(org_id=token).set(health.activity)
        ORG_CUSTOMER_HEALTH_SUCCESS_RATIO.labels(org_id=token).set(health.success_rate)
