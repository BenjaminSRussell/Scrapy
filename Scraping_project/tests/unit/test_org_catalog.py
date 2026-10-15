"""#637: org_id on crawl jobs and profiles. Default org is local.

The catalog filters lists by org. It does not isolate Redis, metrics, or Delta.
"""

from __future__ import annotations

import pytest

from src.utils.org_catalog import (
    DEFAULT_ORG_ID,
    OrgCatalog,
    OrgCatalogError,
    resolve_org_id,
)

pytestmark = pytest.mark.unit


def test_default_org_is_local():
    assert DEFAULT_ORG_ID == "local"
    assert resolve_org_id(None) == "local"
    assert resolve_org_id("") == "local"
    assert resolve_org_id("   ") == "local"


def test_create_profile_and_job_default_to_local():
    catalog = OrgCatalog()
    profile = catalog.create_profile("UConn", profile_id="uconn", domains=("uconn.edu",))
    job = catalog.create_job("uconn", job_id="job-1")
    assert profile.org_id == "local"
    assert job.org_id == "local"
    assert job.profile_id == "uconn"
    assert profile.domains == ("uconn.edu",)


def test_explicit_org_is_stored_on_create():
    catalog = OrgCatalog()
    profile = catalog.create_profile("Example", profile_id="example", org_id="acme")
    job = catalog.create_job("example", job_id="job-2", org_id="acme")
    assert profile.org_id == "acme"
    assert job.org_id == "acme"


def test_omitted_job_org_copies_the_profile_org():
    catalog = OrgCatalog()
    catalog.create_profile("Example", profile_id="example", org_id="acme")
    job = catalog.create_job("example", job_id="job-3")
    assert job.org_id == "acme"


def test_list_defaults_to_local_and_hides_other_orgs():
    catalog = OrgCatalog()
    catalog.create_profile("UConn", profile_id="uconn")
    catalog.create_job("uconn", job_id="local-job")
    catalog.create_profile("Example", profile_id="example", org_id="acme")
    catalog.create_job("example", job_id="acme-job")

    local_profiles = catalog.list_profiles()
    local_jobs = catalog.list_jobs()
    assert [row.profile_id for row in local_profiles] == ["uconn"]
    assert [row.job_id for row in local_jobs] == ["local-job"]

    acme_profiles = catalog.list_profiles("acme")
    acme_jobs = catalog.list_jobs(" acme ")
    assert [row.profile_id for row in acme_profiles] == ["example"]
    assert [row.job_id for row in acme_jobs] == ["acme-job"]
    assert catalog.list_profiles("other") == []
    assert catalog.list_jobs("other") == []


def test_job_org_must_match_its_profile():
    catalog = OrgCatalog()
    catalog.create_profile("UConn", profile_id="uconn")
    with pytest.raises(OrgCatalogError, match="does not match profile"):
        catalog.create_job("uconn", org_id="acme")
    assert catalog.list_jobs() == []


def test_unknown_profile_and_duplicate_ids_are_rejected():
    catalog = OrgCatalog()
    catalog.create_profile("UConn", profile_id="uconn")
    with pytest.raises(OrgCatalogError, match="unknown profile"):
        catalog.create_job("missing")
    with pytest.raises(OrgCatalogError, match="profile already exists"):
        catalog.create_profile("Again", profile_id="uconn")
    catalog.create_job("uconn", job_id="job-1")
    with pytest.raises(OrgCatalogError, match="job already exists"):
        catalog.create_job("uconn", job_id="job-1")


def test_bad_org_id_is_rejected():
    catalog = OrgCatalog()
    with pytest.raises(OrgCatalogError, match="org_id"):
        catalog.create_profile("UConn", org_id="has space")
    with pytest.raises(OrgCatalogError, match="org_id"):
        catalog.create_profile("UConn", org_id="-leading")
    with pytest.raises(OrgCatalogError, match="org_id must be a string"):
        catalog.create_profile("UConn", org_id=True)  # type: ignore[arg-type]
    assert catalog.list_profiles() == []


def test_document_round_trip_keeps_org_id():
    catalog = OrgCatalog()
    catalog.create_profile("UConn", profile_id="uconn", domains=("uconn.edu",))
    catalog.create_profile("Example", profile_id="example", org_id="acme", domains=("example.com",))
    catalog.create_job("uconn", job_id="local-job")
    catalog.create_job("example", job_id="acme-job", org_id="acme")

    document = catalog.to_document()
    assert document["profiles"][0]["org_id"] == "local"
    assert document["jobs"][1]["org_id"] == "acme"
    restored = OrgCatalog.load_document(document)
    assert [row.org_id for row in restored.list_profiles("local")] == ["local"]
    assert [row.job_id for row in restored.list_jobs("acme")] == ["acme-job"]
    assert restored.list_profiles("acme")[0].domains == ("example.com",)


def test_document_missing_org_id_is_rejected():
    with pytest.raises(OrgCatalogError, match="profile keys"):
        OrgCatalog.load_document(
            {"profiles": [{"profile_id": "uconn", "name": "UConn", "domains": []}], "jobs": []}
        )
