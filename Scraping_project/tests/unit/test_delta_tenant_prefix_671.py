"""#671: per-tenant Delta directory is flag-gated and off by default."""

from __future__ import annotations

import pytest

from src.core.config import Config
from src.lakehouse.lakehouse_manager import (
    LakehouseManager,
    resolve_delta_tenant_id,
    tenant_path_prefix_enabled,
)
from src.utils.delta import DeltaHelper

pytestmark = pytest.mark.unit

_SITE_ENV = ("SITE_ID", "TENANT_ID", "REDIS_SITE_ID", "DELTA_TENANT_PATH_PREFIX")


def _clear_site_env(monkeypatch):
    for name in _SITE_ENV:
        monkeypatch.delenv(name, raising=False)


def _manager(tmp_path, monkeypatch, **env):
    _clear_site_env(monkeypatch)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))
    mgr = LakehouseManager(start_workers=False)
    return mgr


def test_flag_off_keeps_tables_on_the_root(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch, SITE_ID="uconn")
    try:
        root = tmp_path / "lake"
        assert mgr.tenant_id is None
        assert mgr.base_path == root
        assert mgr.tables["stage2_page_analysis"] == root / "stage2_page_analysis"
        assert mgr.spill_path == root / "_write_spill"
        assert not (root / "uconn").exists()
    finally:
        mgr.shutdown()


def test_flag_on_prefixes_resolved_root_and_not_an_explicit_path(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch, DELTA_TENANT_PATH_PREFIX="true", SITE_ID="uconn")
    try:
        root = tmp_path / "lake"
        tenant = root / "uconn"
        assert mgr.tenant_id == "uconn"
        assert mgr.base_path == tenant
        assert mgr.tables["stage2_queue"] == tenant / "stage2_queue"
        assert mgr.spill_path == tenant / "_write_spill"
        assert (tenant / "stage2_queue").is_dir()
        assert not (root / "stage2_queue").exists()
    finally:
        mgr.shutdown()

    explicit = LakehouseManager(base_path=str(tmp_path / "explicit"), start_workers=False)
    try:
        assert explicit.tenant_id is None
        assert explicit.base_path == tmp_path / "explicit"
        assert not (tmp_path / "explicit" / "uconn").exists()
    finally:
        explicit.shutdown()


def test_helper_and_manager_share_the_prefixed_root(tmp_path, monkeypatch):
    _clear_site_env(monkeypatch)
    monkeypatch.setenv("DELTA_TENANT_PATH_PREFIX", "1")
    monkeypatch.setenv("TENANT_ID", "acme")
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))
    helper = DeltaHelper()
    mgr = LakehouseManager(start_workers=False)
    try:
        assert helper.base_path == tmp_path / "lake" / "acme"
        assert mgr.base_path == helper.base_path
        assert mgr.tables["stage2_page_analysis"] == helper.base_path / "stage2_page_analysis"
    finally:
        mgr.shutdown()
    # A helper constructed with a path must not prefix a second time.
    pointed = DeltaHelper(tmp_path / "lake" / "acme")
    assert pointed.base_path == tmp_path / "lake" / "acme"


def test_site_id_beats_tenant_id_and_default_is_default(tmp_path, monkeypatch):
    mgr = _manager(
        tmp_path,
        monkeypatch,
        DELTA_TENANT_PATH_PREFIX="yes",
        SITE_ID="uconn",
        TENANT_ID="acme",
    )
    try:
        assert mgr.base_path == tmp_path / "lake" / "uconn"
    finally:
        mgr.shutdown()

    _clear_site_env(monkeypatch)
    monkeypatch.setenv("DELTA_TENANT_PATH_PREFIX", "on")
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "other"))
    bare = LakehouseManager(start_workers=False)
    try:
        assert bare.tenant_id == "default"
        assert bare.base_path == tmp_path / "other" / "default"
    finally:
        bare.shutdown()


def test_config_flag_is_used_when_env_is_unset_and_env_false_wins(tmp_path, monkeypatch):
    _clear_site_env(monkeypatch)
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))
    monkeypatch.setenv("SITE_ID", "uconn")
    cfg = Config.get_instance()
    previous = cfg.get("delta_lake.tenant_path_prefix", False)
    cfg.set("delta_lake.tenant_path_prefix", True)
    try:
        assert tenant_path_prefix_enabled(cfg) is True
        mgr = LakehouseManager(start_workers=False)
        try:
            assert mgr.base_path == tmp_path / "lake" / "uconn"
        finally:
            mgr.shutdown()
        monkeypatch.setenv("DELTA_TENANT_PATH_PREFIX", "false")
        assert tenant_path_prefix_enabled(cfg) is False
        plain = LakehouseManager(start_workers=False)
        try:
            assert plain.tenant_id is None
            assert plain.base_path == tmp_path / "lake"
        finally:
            plain.shutdown()
    finally:
        cfg.set("delta_lake.tenant_path_prefix", previous)


@pytest.mark.parametrize("bad", ["../evil", "a/b", "..", "has space", "tenant:1"])
def test_unsafe_tenant_id_is_rejected(tmp_path, monkeypatch, bad):
    _clear_site_env(monkeypatch)
    monkeypatch.setenv("DELTA_TENANT_PATH_PREFIX", "true")
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))
    monkeypatch.setenv("SITE_ID", bad)
    with pytest.raises(ValueError, match="671"):
        LakehouseManager(start_workers=False)
    assert not (tmp_path / "lake").exists()


def test_bad_flag_value_raises_and_writes_nothing(tmp_path, monkeypatch):
    _clear_site_env(monkeypatch)
    monkeypatch.setenv("DELTA_TENANT_PATH_PREFIX", "maybe")
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))
    with pytest.raises(ValueError, match="tenant_path_prefix"):
        LakehouseManager(start_workers=False)
    assert not (tmp_path / "lake").exists()


def test_resolve_delta_tenant_id_matches_site_id(monkeypatch):
    _clear_site_env(monkeypatch)
    monkeypatch.setenv("REDIS_SITE_ID", "lab")
    assert resolve_delta_tenant_id() == "lab"
