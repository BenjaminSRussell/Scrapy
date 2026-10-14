"""#191: seen-set keys are site:{id}:{prefix}:urls and two sites do not share membership."""

import pytest
import fakeredis

from src.core.constants import REDIS_KEY_SEEN_URLS
from src.utils.redis import (
    DEFAULT_SITE_ID,
    SEEN_KEY_MIGRATION,
    RedisHelper,
    resolve_site_id,
    seen_set_key,
)
from src.utils.url_canon import canonical_or_raw

_SITE_ENV = ("SITE_ID", "TENANT_ID", "REDIS_SITE_ID")
URL = "https://UConn.EDU/Admissions/?utm_source=email"
CANON = "https://uconn.edu/admissions"


def _clear_site_env(monkeypatch):
    for name in _SITE_ENV:
        monkeypatch.delenv(name, raising=False)


def _helper(server, site_id=None):
    helper = RedisHelper(site_id=site_id)
    helper._client = fakeredis.FakeRedis(server=server, decode_responses=True)
    return helper


@pytest.fixture
def server():
    return fakeredis.FakeServer()


def test_seen_key_format_is_site_id_prefix_urls(monkeypatch):
    _clear_site_env(monkeypatch)
    assert DEFAULT_SITE_ID == "default"
    assert seen_set_key("seen") == "site:default:seen:urls"
    assert seen_set_key("scout") == "site:default:scout:urls"
    assert seen_set_key("seen", site_id="uconn") == "site:uconn:seen:urls"
    assert seen_set_key("scout", site_id="uconn") == "site:uconn:scout:urls"
    assert REDIS_KEY_SEEN_URLS == "site:default:seen:urls"
    assert resolve_site_id("  tenant_1 ") == "tenant_1"


def test_two_sites_have_independent_seen_sets(server, monkeypatch):
    _clear_site_env(monkeypatch)
    shared = _helper(server)
    assert shared.claim_url(URL, site_id="alpha") is True
    assert shared.claim_url(URL, site_id="beta") is True  # other site, still first seen
    assert shared.claim_url(URL, site_id="alpha") is False
    assert shared.check_url_seen("https://uconn.edu/admissions", site_id="alpha") is True
    assert shared.check_url_seen("https://uconn.edu/admissions", site_id="beta") is True
    assert shared.check_url_seen("https://uconn.edu/admissions", site_id="gamma") is False

    assert shared.client.sismember("site:alpha:seen:urls", CANON)
    assert shared.client.sismember("site:beta:seen:urls", CANON)
    assert not shared.client.exists("site:gamma:seen:urls")
    assert not shared.client.exists("seen:urls")
    assert not shared.client.exists("scout:urls")
    assert canonical_or_raw(URL) == CANON

    # A tracking variant is the same page inside one site and invisible to the other.
    fresh = "https://yale.edu/about"
    assert shared.mark_url_seen("https://Yale.edu/about/?utm_medium=x", site_id="alpha") is True
    assert shared.check_url_seen(fresh, site_id="alpha") is True
    assert shared.check_url_seen(fresh, site_id="beta") is False


def test_two_helpers_on_one_server_do_not_share_membership(server):
    alpha = _helper(server, site_id="alpha")
    beta = _helper(server, site_id="beta")
    again = _helper(server, site_id="alpha")
    url = "https://example.edu/catalog"
    assert alpha.claim_url(url) is True
    assert beta.claim_url(url) is True
    assert again.claim_url(url) is False
    assert again.check_url_seen(url) is True
    assert beta.check_url_seen(url) is True
    assert alpha.check_url_seen(url, site_id="beta") is True
    assert alpha.client.sismember("site:alpha:seen:urls", url)
    assert alpha.client.sismember("site:beta:seen:urls", url)
    owned = alpha.claim_urls(
        ["https://example.edu/a", "https://example.edu/b"], key_prefix="scout"
    )
    assert owned == ["https://example.edu/a", "https://example.edu/b"]
    assert beta.claim_urls(
        ["https://example.edu/a", "https://example.edu/b"], key_prefix="scout"
    ) == ["https://example.edu/a", "https://example.edu/b"]
    assert alpha.client.sismember("site:alpha:scout:urls", "https://example.edu/a")
    assert not alpha.client.exists("scout:urls")


def test_same_site_still_isolates_prefixes(server, monkeypatch):
    _clear_site_env(monkeypatch)
    helper = _helper(server)
    url = "https://example.edu/p"
    assert helper.claim_url(url, "scout", site_id="uconn") is True
    assert helper.claim_url(url, "deep_dive", site_id="uconn") is True
    assert helper.check_url_seen(url, "scout", site_id="uconn") is True
    assert helper.client.sismember("site:uconn:scout:urls", url)
    assert helper.client.sismember("site:uconn:deep_dive:urls", url)
    assert not helper.client.exists("scout:urls")


def test_env_precedence_and_explicit_override(server, monkeypatch):
    _clear_site_env(monkeypatch)
    monkeypatch.setenv("REDIS_SITE_ID", "from_redis")
    monkeypatch.setenv("TENANT_ID", "from_tenant")
    monkeypatch.setenv("SITE_ID", "from_site")
    helper = _helper(server)
    assert helper.seen_key("seen") == "site:from_site:seen:urls"
    helper.mark_url_seen("https://example.edu/env")
    assert helper.client.sismember("site:from_site:seen:urls", "https://example.edu/env")

    monkeypatch.delenv("SITE_ID")
    assert _helper(server).seen_key() == "site:from_tenant:seen:urls"
    monkeypatch.delenv("TENANT_ID")
    assert _helper(server).seen_key("scout") == "site:from_redis:scout:urls"

    pinned = _helper(server, site_id="pinned")
    assert pinned.seen_key("seen") == "site:pinned:seen:urls"
    assert pinned.seen_key("seen", site_id="call") == "site:call:seen:urls"
    pinned.claim_url("https://example.edu/pin", site_id="call")
    assert pinned.client.sismember("site:call:seen:urls", "https://example.edu/pin")
    assert not pinned.client.exists("site:from_redis:seen:urls")


def test_invalid_site_id_writes_nothing(server, monkeypatch):
    _clear_site_env(monkeypatch)
    helper = _helper(server)
    for bad in ("a:b", "bad id", "*", "site/uconn", "has\nnewline", "."):
        with pytest.raises(ValueError):
            helper.claim_url("https://example.edu/nope", site_id=bad)
    with pytest.raises(ValueError):
        seen_set_key("seen", site_id="a:b")
    assert helper.client.dbsize() == 0
    # A blank id is unset, not invalid: it falls through to the default site.
    assert helper.claim_url("https://example.edu/nope", site_id="   ") is True
    assert helper.client.sismember("site:default:seen:urls", "https://example.edu/nope")
    assert not helper.client.exists("seen:urls")
    assert helper.client.dbsize() == 1


def test_migration_note_covers_unscoped_keys():
    note = SEEN_KEY_MIGRATION
    assert "seen:urls" in note
    assert "scout:urls" in note
    assert "site:{id}:seen:urls" in note
    assert "site:default:seen:urls" in note
    assert "RENAME seen:urls site:default:seen:urls" in note
    assert "no automatic" in note.lower()
    assert "not read" in note.lower() or "no longer reads" in note.lower()
    assert "SITE_ID" in note and "TENANT_ID" in note
    assert "default" in note
