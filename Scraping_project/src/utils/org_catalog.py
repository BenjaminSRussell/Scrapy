"""Org label on crawl jobs and crawl profiles (#637).

``org_id`` is stored on the product records and used to filter lists. It is
not an access grant, not a Redis key segment (#191), not a metric label
(#192), and not a Delta partition or column (#193). Seen-sets, queues, and
lake tables stay shared.

A missing or blank ``org_id`` on create is ``local``. That string is not the
site-id fallback ``default``. Listing with no org returns only ``local``, so
another org's records stay out of the default view. A job uses its profile's
org. Passing a different org on the job is rejected. Nothing here is durable:
the catalog is the caller's object, and a process restart drops it. Durable
jobs stay with the crawl job API (#147).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

DEFAULT_ORG_ID = "local"

# Same character class as a site id, so a later join does not need a second
# pattern. ``local`` matches. Case is significant.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_PROFILE_KEYS = frozenset({"profile_id", "name", "org_id", "domains"})
_JOB_KEYS = frozenset({"job_id", "profile_id", "org_id"})


class OrgCatalogError(ValueError):
    """A create, list, or document that this stub will not accept."""


def resolve_org_id(org_id: str | None) -> str:
    """``None`` or blank becomes ``local``. Any other value must match the id pattern."""
    if org_id is None:
        return DEFAULT_ORG_ID
    if isinstance(org_id, bool) or not isinstance(org_id, str):
        raise OrgCatalogError("org_id must be a string")
    text = org_id.strip()
    if not text:
        return DEFAULT_ORG_ID
    if _ID_RE.fullmatch(text) is None:
        raise OrgCatalogError("org_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}")
    return text


def _require_id(value: str | None, label: str) -> str:
    if value is None:
        return uuid4().hex
    if isinstance(value, bool) or not isinstance(value, str):
        raise OrgCatalogError(f"{label} must be a string")
    text = value.strip()
    if _ID_RE.fullmatch(text) is None:
        raise OrgCatalogError(f"{label} must match [A-Za-z0-9][A-Za-z0-9._-]{{0,127}}")
    return text


def _require_name(name: str) -> str:
    if isinstance(name, bool) or not isinstance(name, str):
        raise OrgCatalogError("name must be a string")
    text = name.strip()
    if not text or len(text) > 200:
        raise OrgCatalogError("name must be 1..200 characters")
    return text


def _require_domains(domains: Sequence[str] | None) -> tuple[str, ...]:
    if domains is None:
        return ()
    if isinstance(domains, (str, bytes)) or not isinstance(domains, Sequence):
        raise OrgCatalogError("domains must be a sequence of strings")
    cleaned: list[str] = []
    for item in domains:
        if isinstance(item, bool) or not isinstance(item, str):
            raise OrgCatalogError("domains must be a sequence of strings")
        text = item.strip()
        if not text or any(ch.isspace() for ch in text) or len(text) > 253:
            raise OrgCatalogError("domain must be a non-empty host without spaces")
        cleaned.append(text)
    return tuple(cleaned)


@dataclass(frozen=True, slots=True)
class CrawlProfile:
    """One named crawl profile. ``org_id`` is a label, not a grant."""

    profile_id: str
    name: str
    org_id: str
    domains: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "org_id": self.org_id,
            "domains": list(self.domains),
        }


@dataclass(frozen=True, slots=True)
class CrawlJob:
    """One crawl job. ``org_id`` matches the profile it was created from."""

    job_id: str
    profile_id: str
    org_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "profile_id": self.profile_id,
            "org_id": self.org_id,
        }


@dataclass
class OrgCatalog:
    """In-memory jobs and profiles. Not a store and not tenant isolation."""

    profiles: dict[str, CrawlProfile] = field(default_factory=dict)
    jobs: dict[str, CrawlJob] = field(default_factory=dict)

    def create_profile(
        self,
        name: str,
        *,
        profile_id: str | None = None,
        org_id: str | None = None,
        domains: Sequence[str] | None = None,
    ) -> CrawlProfile:
        """Store a profile. Omitted or blank ``org_id`` is ``local``."""
        chosen_id = _require_id(profile_id, "profile_id")
        if chosen_id in self.profiles:
            raise OrgCatalogError(f"profile already exists: {chosen_id}")
        record = CrawlProfile(
            profile_id=chosen_id,
            name=_require_name(name),
            org_id=resolve_org_id(org_id),
            domains=_require_domains(domains),
        )
        self.profiles[chosen_id] = record
        return record

    def create_job(
        self,
        profile_id: str,
        *,
        job_id: str | None = None,
        org_id: str | None = None,
    ) -> CrawlJob:
        """Store a job for an existing profile.

        Omitted or blank ``org_id`` copies the profile's org. An explicit org
        that is not the profile's org is rejected. The call does not move the
        profile, and it does not partition Redis or the lake.
        """
        if isinstance(profile_id, bool) or not isinstance(profile_id, str):
            raise OrgCatalogError("profile_id must be a string")
        profile_key = profile_id.strip()
        profile = self.profiles.get(profile_key)
        if profile is None:
            raise OrgCatalogError(f"unknown profile: {profile_key}")
        if org_id is None or (isinstance(org_id, str) and not org_id.strip()):
            chosen_org = profile.org_id
        else:
            chosen_org = resolve_org_id(org_id)
        if chosen_org != profile.org_id:
            raise OrgCatalogError(
                f"job org_id {chosen_org} does not match profile org_id {profile.org_id}"
            )
        chosen_id = _require_id(job_id, "job_id")
        if chosen_id in self.jobs:
            raise OrgCatalogError(f"job already exists: {chosen_id}")
        record = CrawlJob(job_id=chosen_id, profile_id=profile.profile_id, org_id=chosen_org)
        self.jobs[chosen_id] = record
        return record

    def list_profiles(self, org_id: str | None = None) -> list[CrawlProfile]:
        """Profiles whose ``org_id`` is ``org_id``, or ``local`` when omitted."""
        chosen = resolve_org_id(org_id)
        return [row for row in self.profiles.values() if row.org_id == chosen]

    def list_jobs(self, org_id: str | None = None) -> list[CrawlJob]:
        """Jobs whose ``org_id`` is ``org_id``, or ``local`` when omitted."""
        chosen = resolve_org_id(org_id)
        return [row for row in self.jobs.values() if row.org_id == chosen]

    def to_document(self) -> dict[str, Any]:
        """JSON-ready records. ``org_id`` is always present."""
        return {
            "profiles": [row.as_dict() for row in self.profiles.values()],
            "jobs": [row.as_dict() for row in self.jobs.values()],
        }

    @classmethod
    def load_document(cls, document: Mapping[str, Any]) -> OrgCatalog:
        """Rebuild a catalog. A stored record must already carry ``org_id``."""
        if not isinstance(document, Mapping):
            raise OrgCatalogError("catalog document must be a mapping")
        extra = set(document) - {"profiles", "jobs"}
        if extra:
            raise OrgCatalogError(f"unknown catalog keys: {sorted(extra)}")
        catalog = cls()
        profiles = document.get("profiles", [])
        jobs = document.get("jobs", [])
        if not isinstance(profiles, list) or not isinstance(jobs, list):
            raise OrgCatalogError("profiles and jobs must be lists")
        for row in profiles:
            _load_profile(catalog, row)
        for row in jobs:
            _load_job(catalog, row)
        return catalog


def _mapping_keys(row: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    extra = set(row) - allowed
    missing = allowed - set(row)
    if extra or missing:
        raise OrgCatalogError(f"{label} keys must be {sorted(allowed)}")


def _load_profile(catalog: OrgCatalog, row: Any) -> None:
    if not isinstance(row, Mapping):
        raise OrgCatalogError("profile must be a mapping")
    _mapping_keys(row, _PROFILE_KEYS, "profile")
    catalog.create_profile(
        row["name"],
        profile_id=row["profile_id"],
        org_id=row["org_id"],
        domains=row["domains"],
    )


def _load_job(catalog: OrgCatalog, row: Any) -> None:
    if not isinstance(row, Mapping):
        raise OrgCatalogError("job must be a mapping")
    _mapping_keys(row, _JOB_KEYS, "job")
    catalog.create_job(row["profile_id"], job_id=row["job_id"], org_id=row["org_id"])
