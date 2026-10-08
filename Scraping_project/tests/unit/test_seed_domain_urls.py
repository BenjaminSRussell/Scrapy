"""Domain URL side table is config/profile driven, not hard-coded to UConn (#317)."""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.lakehouse import seed_manager as smod
from src.lakehouse.lakehouse_manager import InMemoryBackend
from src.lakehouse.seed_manager import SeedManager, domain_urls_policy, host_in_domains

SRC = Path(__file__).resolve().parents[2] / "src"


class FakeConfig:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _urls(backend, table):
    return sorted(r["url"] for r in backend.tables.get(table, []))


@pytest.mark.parametrize(
    "host,expected",
    [
        ("uconn.edu", True),
        ("www.uconn.edu", True),
        ("UCONN.EDU.", True),
        ("notuconn.edu", False),
        ("uconn.edu.evil.com", False),
        ("evil.com", False),
        ("", False),
    ],
)
def test_host_match_is_exact_or_subdomain(host, expected):
    assert host_in_domains(host, ("uconn.edu",)) is expected


def test_lookalike_hosts_no_longer_land_in_the_domain_table():
    """The old ``"uconn.edu" in netloc`` test accepted both look-alikes."""
    backend = InMemoryBackend()
    sm = SeedManager(backend, write_domain_urls=True, allowed_domains=["uconn.edu"])
    result = sm.add_urls_to_seeds(
        ["https://uconn.edu.evil.com/x", "https://notuconn.edu/", "https://www.uconn.edu/a"],
        source_url="s", source_spider="scout",
    )
    assert _urls(backend, "uconn_urls") == ["https://www.uconn.edu/a"]
    assert result["domain_inserted"] == result["uconn_inserted"] == 1
    assert len(backend.tables["seed_urls"]) == 3  # seeds are unaffected


def test_non_uconn_profile_writes_no_domain_table_by_default():
    cfg = FakeConfig({"stage1.allowed_domains": ["example.org"]})
    backend = InMemoryBackend()
    sm = SeedManager(backend, config=cfg)
    assert sm.write_domain_urls is False
    result = sm.add_urls_to_seeds(["https://example.org/a"], source_url="s", source_spider="scout")
    assert result["domain_inserted"] == 0
    assert "uconn_urls" not in backend.tables and _urls(backend, "seed_urls") == ["https://example.org/a"]


def test_profile_opts_in_with_its_own_domains_and_table():
    cfg = FakeConfig({
        "stage1.write_domain_urls": "true",
        "stage1.allowed_domains": ["Example.org", "*.example.net"],
        "stage1.domain_urls_table": "example_urls",
    })
    backend = InMemoryBackend()
    sm = SeedManager(backend, config=cfg)
    sm.add_urls_to_seeds(
        ["https://example.org/a", "https://cdn.example.net/b", "https://uconn.edu/c"],
        source_url="s", source_spider="scout",
    )
    assert _urls(backend, "example_urls") == ["https://cdn.example.net/b", "https://example.org/a"]
    assert "uconn_urls" not in backend.tables


def test_bundled_uconn_profile_still_writes_uconn_urls():
    policy = domain_urls_policy()  # repo config.yml
    assert policy == {"write_domain_urls": True, "allowed_domains": ("uconn.edu",), "domain_urls_table": "uconn_urls"}
    backend = InMemoryBackend()
    SeedManager(backend).add_urls_to_seeds(["https://admissions.uconn.edu/x"], source_url="s", source_spider="scout")
    assert _urls(backend, "uconn_urls") == ["https://admissions.uconn.edu/x"]


def test_per_call_override_beats_config():
    backend = InMemoryBackend()
    sm = SeedManager(backend, write_domain_urls=True, allowed_domains=["uconn.edu"])
    sm.add_urls_to_seeds(["https://uconn.edu/a"], source_url="s", source_spider="x", write_domain_urls=False)
    assert "uconn_urls" not in backend.tables


def test_legacy_write_uconn_urls_alias_still_works_but_warns():
    backend = InMemoryBackend()
    sm = SeedManager(backend, write_domain_urls=False, allowed_domains=["uconn.edu"])
    with pytest.warns(DeprecationWarning, match="write_domain_urls"):
        sm.add_urls_to_seeds(["https://uconn.edu/a"], source_url="s", source_spider="x", write_uconn_urls=True)
    assert _urls(backend, "uconn_urls") == ["https://uconn.edu/a"]


def test_flag_without_domains_skips_with_warning(caplog):
    backend = InMemoryBackend()
    sm = SeedManager(backend, write_domain_urls=True, allowed_domains=[])
    sm.add_urls_to_seeds(["https://uconn.edu/a"], source_url="s", source_spider="x")
    assert "no allowed_domains" in caplog.text and "uconn_urls" not in backend.tables


def test_no_spider_hard_codes_write_uconn_urls():
    offenders = [str(p.relative_to(SRC)) for p in SRC.rglob("*.py")
                 if "write_uconn_urls=True" in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_scout_lets_config_decide():
    from src.stage1.scout_spider import ScoutSpider

    calls = []

    class FakeSeeds:
        def add_urls_to_seeds(self, **kwargs):
            calls.append(kwargs)
            return {"seed_inserted": 1, "domain_inserted": 0, "uconn_inserted": 0, "stage2_enqueued": 0}

    spider = ScoutSpider.__new__(ScoutSpider)
    spider.seed_manager = FakeSeeds()
    spider._add_urls_to_seeds(["https://example.org/a"], source_url="https://example.org/")
    assert calls and "write_uconn_urls" not in calls[0] and "write_domain_urls" not in calls[0]
    assert "write_uconn_urls" not in inspect.getsource(ScoutSpider._add_urls_to_seeds)


def test_policy_without_config_is_off():
    assert domain_urls_policy(SimpleNamespace(get=lambda k, d=None: None))["write_domain_urls"] is False
    assert smod.DEFAULT_DOMAIN_URLS_TABLE == "uconn_urls"
