"""Nullable tenant_id on lake tables (#670)."""
from __future__ import annotations

import pyarrow as pa
import pytest

from src.core.schemas import DEFAULT_TENANT_ID, SCHEMA_REGISTRY, TENANT_ID_COLUMN
from src.lakehouse import lakehouse_manager as lm
from src.lakehouse.lakehouse_manager import (
    LakehouseManager,
    filter_rows_by_tenant,
    resolve_profile_tenant_id,
)

pytestmark = pytest.mark.delta


def _nullable_string(field) -> None:
    """Delta's ``to_arrow()`` field type is not always a pyarrow DataType."""
    assert field.nullable
    assert "string" in str(field.type).lower() or "utf8" in str(field.type).lower()


def test_every_registered_schema_has_nullable_tenant_id():
    assert SCHEMA_REGISTRY
    for name, schema in SCHEMA_REGISTRY.items():
        field = schema.field(TENANT_ID_COLUMN)
        assert field.nullable, name
        assert pa.types.is_string(field.type), name


def test_profile_tenant_precedence(monkeypatch):
    monkeypatch.delenv("DELTA_TENANT_ID", raising=False)
    assert resolve_profile_tenant_id("acme") == "acme"
    assert resolve_profile_tenant_id("  ") == DEFAULT_TENANT_ID
    assert resolve_profile_tenant_id("bad id") == DEFAULT_TENANT_ID

    monkeypatch.setenv("DELTA_TENANT_ID", "from-env")
    assert resolve_profile_tenant_id() == "from-env"
    assert resolve_profile_tenant_id("row") == "row"

    monkeypatch.setenv("DELTA_TENANT_ID", "bad id")

    class _Cfg:
        def get(self, key, default=None):
            if key == "delta_lake.tenant_id":
                return "from-config"
            return default

    monkeypatch.setattr(lm.Config, "get_instance", classmethod(lambda cls: _Cfg()))
    # A set-but-invalid env does not fall through to config.
    assert resolve_profile_tenant_id() == DEFAULT_TENANT_ID

    monkeypatch.delenv("DELTA_TENANT_ID", raising=False)
    assert resolve_profile_tenant_id() == "from-config"


def test_stamp_keeps_explicit_and_fills_missing(monkeypatch):
    monkeypatch.delenv("DELTA_TENANT_ID", raising=False)
    rows = [
        {"url": "https://a.example/1"},
        {"url": "https://a.example/2", "tenant_id": "uconn"},
        {"url": "https://a.example/3", "tenant_id": ""},
        {"url": "https://a.example/4", "tenant_id": "nope id"},
    ]
    gone = LakehouseManager._enrich_records("stage2_queue", rows, None)
    assert gone == []
    assert [r["tenant_id"] for r in rows] == ["default", "uconn", "default", "default"]
    assert all(r["_stage"] == "stage2_queue" for r in rows)


def test_filter_default_includes_legacy_and_other_tenants_do_not():
    rows = [
        {"url": "legacy"},
        {"url": "blank", "tenant_id": "  "},
        {"url": "home", "tenant_id": "default"},
        {"url": "other", "tenant_id": "acme"},
    ]
    assert [r["url"] for r in filter_rows_by_tenant(rows, "default")] == ["legacy", "blank", "home"]
    assert [r["url"] for r in filter_rows_by_tenant(rows, "acme")] == ["other"]
    assert [r["url"] for r in filter_rows_by_tenant(rows, "acme", include_legacy=True)] == [
        "legacy", "blank", "other",
    ]


def test_new_write_stamps_tenant_and_evolution_leaves_old_rows_null(tmp_path, monkeypatch):
    monkeypatch.delenv("DELTA_TENANT_ID", raising=False)
    lake = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    try:
        path = lake.get_table_path("stage2_queue")
        pa_table = pa.table({"url": ["https://old.example/"], "url_hash": ["h0"], "status": ["pending"]})
        from deltalake import write_deltalake

        write_deltalake(str(path), pa_table)
        assert lake.write(
            "stage2_queue",
            [{"url": "https://new.example/", "url_hash": "h1", "status": "pending", "tenant_id": "uconn"},
             {"url": "https://def.example/", "url_hash": "h2", "status": "pending"}],
            async_write=False,
        ) is not False

        field = lake.get_table_schema("stage2_queue").field("tenant_id")
        _nullable_string(field)
        by_hash = {r["url_hash"]: r.get("tenant_id") for r in lake.read("stage2_queue")}
        assert by_hash == {"h0": None, "h1": "uconn", "h2": "default"}

        legacy = lake.read_for_tenant("stage2_queue", "default", columns=["url_hash"])
        assert sorted(r["url_hash"] for r in legacy) == ["h0", "h2"]
        assert lake.read_for_tenant("stage2_queue", "uconn", columns=["url_hash"]) == [{"url_hash": "h1"}]

        assert lake.write(
            "seed_urls",
            [{"url": "https://seed.example/", "domain": "seed.example", "seed_priority": 1, "is_active": True}],
            async_write=False,
        ) is not False
        created = lake.get_table_schema("seed_urls").field("tenant_id")
        _nullable_string(created)
        assert lake.read("seed_urls")[0]["tenant_id"] == "default"
    finally:
        lake.shutdown(timeout=1)
