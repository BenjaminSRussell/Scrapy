"""#458: undomainable URLs are quarantined, not partitioned as domain="unknown"."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.lakehouse import lakehouse_manager as lm
from src.lakehouse.lakehouse_manager import LakehouseManager, partition_domain

TABLE = "stage1_discovery"


@pytest.mark.parametrize("url,expected", [
    ("https://www.cs.uconn.edu/a", "uconn.edu"),
    ("http://192.168.1.1/x", "192.168.1.1"),
    ("http://localhost:8080/", "localhost"),
    ("", None),
    (None, None),
    ("not a url", None),
    ("mailto:someone@uconn.edu", None),
    ("javascript:void(0)", None),
    ("https:///nohost", None),
    ("ftp://files.uconn.edu/x", None),
    ("http://[::1", None),  # urlparse ValueError
])
def test_partition_domain(url, expected):
    assert partition_domain(url) == expected


def _count(table):
    if lm.DELTA_UNKNOWN_DOMAIN is None:
        return None
    return lm.DELTA_UNKNOWN_DOMAIN.labels(table=table)._value.get()


@pytest.fixture
def mgr(tmp_path):
    m = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    yield m
    m.shutdown()


def _partitions(mgr, table):
    path = Path(mgr.get_table_path(table))
    return sorted(p.name for p in path.iterdir() if p.name.startswith("domain="))


def test_write_quarantines_undomainable_rows(mgr):
    before = _count(TABLE)
    rows = [
        {"url": "https://uconn.edu/a", "url_hash": "1"},
        {"url": "not a url", "url_hash": "2"},
        {"url": "mailto:x@uconn.edu", "url_hash": "3"},
        {"url": "https://www.cs.uconn.edu/b", "url_hash": "4", "domain": "unknown"},  # re-derived
    ]
    assert mgr._write_sync(TABLE, rows, "append") is True
    assert sorted(r["url_hash"] for r in mgr.read(TABLE)) == ["1", "4"]
    assert _partitions(mgr, TABLE) == ["domain=uconn.edu"]  # no domain=unknown
    q = mgr.read(lm.DOMAIN_QUARANTINE_TABLE)
    assert sorted(r["url"] for r in q) == ["mailto:x@uconn.edu", "not a url"]
    assert {r["source_table"] for r in q} == {TABLE} and {r["reason"] for r in q} == {"no_usable_host"}
    assert '"url_hash": "2"' in next(r["row_json"] for r in q if r["url"] == "not a url")
    if before is not None:
        assert _count(TABLE) == before + 2


def test_all_bad_batch_is_handled_without_creating_the_table(mgr):
    assert mgr._write_sync(TABLE, [{"url": "", "url_hash": "x"}], "append") is True
    assert not (Path(mgr.get_table_path(TABLE)) / "_delta_log").exists()
    assert len(mgr.read(lm.DOMAIN_QUARANTINE_TABLE)) == 1


def test_merge_path_quarantines_too(mgr):
    n = mgr.merge_into(TABLE, [{"url": "https://uconn.edu/m", "url_hash": "m1"},
                               {"url": "garbage", "url_hash": "m2"}], merge_key="url_hash",
                       update_columns=["url"])
    assert n == 1
    assert [r["url_hash"] for r in mgr.read(TABLE)] == ["m1"]
    assert [r["url"] for r in mgr.read(lm.DOMAIN_QUARANTINE_TABLE)] == ["garbage"]


def test_unpartitioned_tables_are_untouched(mgr):
    assert mgr._write_sync("errors_t", [{"url": "not a url", "msg": "x"}], "append") is True
    assert mgr.read("errors_t")[0]["url"] == "not a url"
    assert "domain" not in mgr.read("errors_t")[0]


def test_repair_job_rewrites_legacy_unknown_partition(mgr):
    from deltalake import write_deltalake
    import pyarrow as pa

    # Legacy state: rows written under domain="unknown" by the old code.
    mgr._write_sync(TABLE, [{"url": "https://uconn.edu/ok", "url_hash": "ok"}], "append")
    path = str(mgr.get_table_path(TABLE))
    schema = mgr._table_schema(Path(path))
    legacy = [{"url": "https://www.uconn.edu/fixable", "url_hash": "f", "domain": "unknown"},
              {"url": "not a url", "url_hash": "b", "domain": "unknown"}]
    cols = {f.name: [r.get(f.name) for r in legacy] for f in schema}
    write_deltalake(path, pa.table(cols, schema=schema), mode="append", partition_by=["domain"])
    assert "domain=unknown" in _partitions(mgr, TABLE)

    assert mgr.repair_unknown_domains(TABLE) == {"rows": 2, "repairable": 1, "quarantine": 1}
    assert "domain=unknown" in _partitions(mgr, TABLE)  # dry run changed nothing

    assert mgr.repair_unknown_domains(TABLE, apply=True) == {"rows": 2, "repairable": 1, "quarantine": 1}
    rows = {r["url_hash"]: r["domain"] for r in mgr.read(TABLE)}
    assert rows == {"ok": "uconn.edu", "f": "uconn.edu"}
    assert [r["url"] for r in mgr.read(lm.DOMAIN_QUARANTINE_TABLE)] == ["not a url"]
    assert mgr.repair_unknown_domains(TABLE) == {"rows": 0, "repairable": 0, "quarantine": 0}


def test_repair_rejects_unpartitioned_tables(mgr):
    with pytest.raises(ValueError):
        mgr.repair_unknown_domains("errors_t")


def test_alert_on_unknown_domain_rate():
    rules = yaml.safe_load((Path(__file__).resolve().parents[2] / "monitoring" / "alerting_rules.yml").read_text())
    alert = next(r for g in rules["groups"] for r in g["rules"] if r.get("alert") == "DeltaUndomainableRows")
    assert "delta_unknown_domain_rows_total" in alert["expr"]
