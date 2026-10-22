"""CrawlDataManager against a real Delta lake in a temp dir: write/read/list contracts (#518).

The unit tests in tests/unit/common use an in-memory FakeDelta, so they cannot catch a
column or table-path regression in the real read path. Here the manager reads what the
lake actually wrote, offline, with no services.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.common.crawl_data_manager import CrawlDataManager
from src.lakehouse.lakehouse_manager import LakehouseManager

pytestmark = [pytest.mark.delta, pytest.mark.unit]

NOW = datetime.now(UTC)


def _discovery(url, domain, depth=1, js=0.0, days_ago=1.0):
    return {
        "url": url,
        "url_hash": url,
        "domain": domain,
        "discovered_at": (NOW - timedelta(days=days_ago)).isoformat(),
        "js_confidence": js,
        "depth": depth,
        "parent_url": None,
    }


@pytest.fixture
def lake(tmp_path):
    manager = LakehouseManager(base_path=str(tmp_path / "lake"), start_workers=False)
    yield manager
    manager.shutdown()


def test_missing_lake_dir_is_created_and_empty_reads_degrade(tmp_path):
    base = tmp_path / "does" / "not" / "exist"
    assert not base.exists()

    lake = LakehouseManager(base_path=str(base), start_workers=False)
    try:
        assert base.is_dir()
        mgr = CrawlDataManager(delta_manager=lake)

        assert mgr.analyze_domain("uconn.edu") is None
        assert mgr.get_top_domains() == []
        assert mgr.get_statistics()["total_urls_discovered"] == 0
        assert mgr._read_table_safe("stage1_discovery", columns=["url"]) == []
    finally:
        lake.shutdown()


def test_written_discovery_and_errors_round_trip_through_analysis(lake):
    rows = [_discovery(f"https://uconn.edu/research/p{i}", "uconn.edu", depth=2, js=0.8) for i in range(4)]
    rows.append(_discovery("https://other.edu/a", "other.edu"))
    assert lake.write("stage1_discovery", rows, async_write=False)
    assert lake.write(
        "stage1_errors",
        [{"url": "https://uconn.edu/broken", "domain": "uconn.edu", "error_type": "timeout"}],
        async_write=False,
    )

    mgr = CrawlDataManager(delta_manager=lake)
    insights = mgr.analyze_domain("uconn.edu")

    assert insights is not None
    assert insights.total_urls_crawled == 4
    assert insights.failed_crawls == 1
    assert insights.successful_crawls == 3
    assert insights.recommended_spider == "js"
    # Paths keep the first two segments, so each page is its own "/research/pN" bucket.
    assert set(insights.most_valuable_paths) == {f"/research/p{i}" for i in range(4)}


def test_read_contract_returns_requested_columns_only(lake):
    assert lake.write("stage1_discovery", [_discovery("https://uconn.edu/a", "uconn.edu")], async_write=False)

    mgr = CrawlDataManager(delta_manager=lake)
    rows = mgr._read_table_safe("stage1_discovery", columns=["url", "domain"])

    assert rows == [{"url": "https://uconn.edu/a", "domain": "uconn.edu"}]


def test_list_contract_reports_written_tables_and_domains(lake):
    assert lake.write(
        "stage1_discovery",
        [_discovery(f"https://uconn.edu/x{i}", "uconn.edu") for i in range(10)]
        + [_discovery("https://other.edu/a", "other.edu")],
        async_write=False,
    )

    assert "stage1_discovery" in {t["name"] for t in lake.list_tables()}

    stats = CrawlDataManager(delta_manager=lake).get_statistics()
    assert stats["total_urls_discovered"] == 11
    assert stats["unique_domains"] == 2

    top = CrawlDataManager(delta_manager=lake).get_top_domains(limit=5)
    assert [domain for domain, _ in top] == ["uconn.edu"]


def test_overwrite_replaces_rows_seen_by_the_manager(lake):
    assert lake.write("stage1_discovery", [_discovery("https://old.edu/a", "old.edu")], async_write=False)
    assert lake.write(
        "stage1_discovery",
        [_discovery("https://new.edu/a", "new.edu")],
        mode="overwrite",
        async_write=False,
    )

    mgr = CrawlDataManager(delta_manager=lake)

    assert mgr.analyze_domain("old.edu") is None
    assert mgr.analyze_domain("new.edu").total_urls_crawled == 1
