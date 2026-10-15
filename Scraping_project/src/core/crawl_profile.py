"""Crawl profile quotas (#672).

A profile may set ``max_concurrent_jobs`` and ``max_urls_per_day``. Null, or a
missing cap, is uncapped. ``admit_job`` reserves one concurrent slot and the
requested URL count for the UTC day, or raises :class:`QuotaExceededError`
without reserving either. Releasing a job frees the slot only; the day's URL
charge stays until the next UTC day.

This is a fairness check. It is not an authorization decision.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

logger = logging.getLogger(__name__)

# Same shape as a site/tenant id (#191). Not a URL, job id, or host.
PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PROFILE_ID_RULE = "[A-Za-z0-9][A-Za-z0-9._-]{0,127}"

REASON_CONCURRENT_JOBS = "max_concurrent_jobs"
REASON_URLS_PER_DAY = "max_urls_per_day"
_NOT_AUTHZ = "This is a quota rejection, not an authorization decision."

_SITE_ID_ENV = "SITE_ID"
_TENANT_ID_ENV = "TENANT_ID"
_REDIS_SITE_ID_ENV = "REDIS_SITE_ID"
_DEFAULT_PROFILE_ID = "default"

_LOCK = threading.Lock()
_ACTIVE: dict[str, int] = {}
_URLS: dict[tuple[str, str], int] = {}

try:
    from prometheus_client import Counter

    QUOTA_EXCEEDED = Counter(
        "quota_exceeded_total",
        "Orchestrator runs rejected because a crawl profile was over quota (#672).",
        ["profile", "reason"],
    )
except Exception:  # prometheus_client missing or metric already registered
    QUOTA_EXCEEDED = None


class QuotaExceededError(RuntimeError):
    """The crawl profile is over ``max_concurrent_jobs`` or ``max_urls_per_day``.

    Not an authorization failure. ``limit`` is the field that failed.
    """

    def __init__(
        self,
        profile_id: str,
        limit: str,
        cap: int,
        observed: int,
        message: str,
        *,
        requested: int | None = None,
    ):
        self.profile_id = profile_id
        self.limit = limit
        self.cap = cap
        self.observed = observed
        self.requested = requested
        super().__init__(message)


class JobAdmission:
    """A run that passed the quota check.

    ``holds_slot`` is false when ``max_concurrent_jobs`` is null: a null cap is
    not tracked, so releasing it must not free some other admission's slot.
    URL spend is not refunded on release.
    """

    def __init__(self, profile_id: str, urls_charged: int, day: str, holds_slot: bool):
        self.profile_id = profile_id
        self.urls_charged = urls_charged
        self.day = day
        self.holds_slot = holds_slot
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        if self.holds_slot:
            release_job(self.profile_id)


def _non_negative_int_or_null(value: object) -> object:
    """Null (uncapped) or an int >= 0. Booleans and other numbers are rejected."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("must be an integer or null")
    if value < 0:
        raise ValueError("must be >= 0")
    return value


class CrawlProfileQuotas(BaseModel):
    """Cap fields on a crawl profile. Null means uncapped (#672)."""

    model_config = ConfigDict(extra="forbid")
    max_concurrent_jobs: int | None = None
    max_urls_per_day: int | None = None

    @field_validator("max_concurrent_jobs", "max_urls_per_day", mode="before")
    @classmethod
    def _cap_is_int_or_null(cls, value: object) -> object:
        return _non_negative_int_or_null(value)


class CrawlProfile(CrawlProfileQuotas):
    """One crawl profile and its quotas."""

    profile_id: str

    @field_validator("profile_id")
    @classmethod
    def _check_profile_id(cls, value: object) -> str:
        if not isinstance(value, str) or PROFILE_ID_RE.fullmatch(value) is None:
            raise ValueError(f"profile id {value!r} must match {PROFILE_ID_RULE}")
        return value


def reset_quota_for_tests() -> None:
    """Drop process-wide reservations. Tests that share the ledger call this."""
    with _LOCK:
        _ACTIVE.clear()
        _URLS.clear()


def quota_usage(profile_id: str, *, now: datetime | None = None) -> tuple[int, int]:
    """``(active jobs, URLs charged on the UTC day of now)``."""
    day = _day(now)
    with _LOCK:
        return _ACTIVE.get(profile_id, 0), _URLS.get((profile_id, day), 0)


def _day(now: datetime | None) -> str:
    moment = now if now is not None else datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("quota clock must be timezone-aware")
    return moment.astimezone(UTC).date().isoformat()


def _profile_label(profile_id: str) -> str:
    if PROFILE_ID_RE.fullmatch(profile_id) is None:
        return "rejected"
    return profile_id


def _mark_exceeded(profile_id: str, reason: str) -> None:
    if QUOTA_EXCEEDED is None:
        return
    QUOTA_EXCEEDED.labels(profile=_profile_label(profile_id), reason=reason).inc()


def _jobs(count: int) -> str:
    return "1 job" if count == 1 else f"{count} jobs"


def _url_noun(count: int) -> str:
    return "1 URL" if count == 1 else f"{count} URLs"


def load_configured_profiles() -> dict[str, Any]:
    """``crawl_profiles`` from config.yml. Unreadable config means no caps."""
    try:
        from src.core.config import get_config

        section = get_config().get_section("crawl_profiles")
    except Exception:
        logger.debug("crawl_profiles unreadable; treating every profile as uncapped", exc_info=True)
        return {}
    return section if isinstance(section, dict) else {}


def _configured_profile_id(explicit: str | None) -> str:
    """Explicit id, else SITE_ID, else TENANT_ID, else REDIS_SITE_ID, else default."""
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    for name in (_SITE_ID_ENV, _TENANT_ID_ENV, _REDIS_SITE_ID_ENV):
        raw = os.getenv(name)
        if raw and raw.strip():
            return raw.strip()
    return _DEFAULT_PROFILE_ID


def resolve_crawl_profile(
    profile: CrawlProfile | str | None = None,
    profiles: Mapping[str, Any] | None = None,
) -> CrawlProfile:
    """The profile a run is billed to.

    An explicit :class:`CrawlProfile` is used as given. Otherwise caps come
    from ``profiles`` (or config.yml). When no site id is set and exactly one
    profile is configured, that profile is the one enforced. Any other missing
    id is uncapped.
    """
    if isinstance(profile, CrawlProfile):
        return profile
    table = dict(profiles) if profiles is not None else load_configured_profiles()
    if profile is None:
        profile_id = _configured_profile_id(None)
        if profile_id not in table and profile_id == _DEFAULT_PROFILE_ID and len(table) == 1:
            profile_id = next(iter(table))
    else:
        profile_id = str(profile).strip()
    if PROFILE_ID_RE.fullmatch(profile_id) is None:
        raise ValueError(f"profile id {profile_id!r} must match {PROFILE_ID_RULE}")
    raw = table.get(profile_id) or {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"crawl profile {profile_id!r} must be a mapping or null")
    try:
        quotas = CrawlProfileQuotas.model_validate(dict(raw))
    except ValidationError as exc:
        raise ValueError(f"crawl profile {profile_id!r} has invalid quotas: {exc}") from exc
    return CrawlProfile(
        profile_id=profile_id,
        max_concurrent_jobs=quotas.max_concurrent_jobs,
        max_urls_per_day=quotas.max_urls_per_day,
    )


def admit_job(
    profile: CrawlProfile,
    urls: int | None,
    *,
    now: datetime | None = None,
) -> JobAdmission:
    """Reserve one job and ``urls`` against the profile, or reject with no spend.

    ``urls is None`` is an unbounded request. That is allowed only when
    ``max_urls_per_day`` is null. A rejection increments
    ``quota_exceeded_total{profile,reason}`` and leaves the ledger unchanged.
    """
    if urls is not None and (isinstance(urls, bool) or not isinstance(urls, int) or urls < 0):
        raise ValueError("urls must be a non-negative integer, or None when the request has no URL limit")
    day = _day(now)
    with _LOCK:
        active = _ACTIVE.get(profile.profile_id, 0)
        job_cap = profile.max_concurrent_jobs
        if job_cap is not None and active + 1 > job_cap:
            _mark_exceeded(profile.profile_id, REASON_CONCURRENT_JOBS)
            raise QuotaExceededError(
                profile.profile_id,
                REASON_CONCURRENT_JOBS,
                job_cap,
                active,
                (
                    f"Quota exceeded for crawl profile {profile.profile_id!r}: "
                    f"max_concurrent_jobs is {job_cap} ({_jobs(active)} already running). {_NOT_AUTHZ}"
                ),
                requested=urls,
            )
        used = _URLS.get((profile.profile_id, day), 0)
        url_cap = profile.max_urls_per_day
        if url_cap is not None and urls is None:
            _mark_exceeded(profile.profile_id, REASON_URLS_PER_DAY)
            raise QuotaExceededError(
                profile.profile_id,
                REASON_URLS_PER_DAY,
                url_cap,
                used,
                (
                    f"Quota exceeded for crawl profile {profile.profile_id!r}: "
                    f"max_urls_per_day is {url_cap} but the request has no URL limit "
                    f"({_url_noun(used)} already used today). {_NOT_AUTHZ}"
                ),
                requested=None,
            )
        if url_cap is not None and urls is not None and used + urls > url_cap:
            _mark_exceeded(profile.profile_id, REASON_URLS_PER_DAY)
            raise QuotaExceededError(
                profile.profile_id,
                REASON_URLS_PER_DAY,
                url_cap,
                used,
                (
                    f"Quota exceeded for crawl profile {profile.profile_id!r}: "
                    f"max_urls_per_day is {url_cap} ({_url_noun(used)} already used today, "
                    f"request is {urls}). {_NOT_AUTHZ}"
                ),
                requested=urls,
            )
        # A null cap is not recorded. Charging it would make a later numeric
        # cap on the same profile id count runs that were uncapped.
        holds_slot = job_cap is not None
        charged = urls if url_cap is not None and urls else 0
        if holds_slot:
            _ACTIVE[profile.profile_id] = active + 1
        if charged:
            _URLS[(profile.profile_id, day)] = used + charged
    return JobAdmission(profile.profile_id, charged, day, holds_slot)


def release_job(profile_id: str) -> None:
    """Free one concurrent slot. Does not refund URLs charged for the day."""
    with _LOCK:
        active = _ACTIVE.get(profile_id, 0)
        if active <= 1:
            _ACTIVE.pop(profile_id, None)
        else:
            _ACTIVE[profile_id] = active - 1
