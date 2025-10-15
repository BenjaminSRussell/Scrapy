"""
Unit tests for PostgresManager.

Avoids real database connections by stubbing psycopg2 connection pooling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.common import postgres_manager as pg_module
from src.common.postgres_manager import PostgresManager


@dataclass
class CursorStub:
    executed: list[tuple[str, tuple[Any, ...] | None]] = field(default_factory=list)
    fetch_values: list[dict[str, Any]] = field(default_factory=list)

    def execute(self, query: str, params: tuple[Any, ...] | None = None):
        self.executed.append((query.strip(), params))

    def fetchall(self):
        return self.fetch_values

    def close(self):
        return None


class ConnectionStub:
    def __init__(self, cursor_factory=None, fetch_values=None):
        self.cursor_factory = cursor_factory
        self.cursor_stub = CursorStub(fetch_values=fetch_values or [])
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, cursor_factory=None):
        return self.cursor_stub

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class PoolStub:
    def __init__(self, connection: ConnectionStub):
        self.connection = connection
        self.put_calls = 0

    def getconn(self):
        return self.connection

    def putconn(self, conn):
        self.put_calls += 1

    def closeall(self):
        return None


@pytest.fixture
def patched_postgres(monkeypatch):
    connection = ConnectionStub()
    pool_stub = PoolStub(connection)

    monkeypatch.setattr(pg_module, "POSTGRES_AVAILABLE", True)

    psycopg = pg_module.psycopg2
    if psycopg is None:
        from types import SimpleNamespace

        psycopg = SimpleNamespace()
        monkeypatch.setattr(pg_module, "psycopg2", psycopg)

    fake_pool = MagicMock(return_value=pool_stub)
    pool_namespace = MagicMock(SimpleConnectionPool=fake_pool)
    monkeypatch.setattr(psycopg, "pool", pool_namespace, raising=False)
    monkeypatch.setattr(pg_module, "pool", pool_namespace, raising=False)
    monkeypatch.setattr(pg_module, "RealDictCursor", MagicMock())

    return connection, pool_stub, fake_pool


def test_init_requires_password(monkeypatch):
    monkeypatch.setattr(pg_module, "POSTGRES_AVAILABLE", True)
    with pytest.raises(ValueError):
        PostgresManager(password=None)


def test_init_initializes_pool_and_schema(patched_postgres, monkeypatch):
    connection, pool_stub, fake_pool = patched_postgres
    init_calls = []

    def mock_init_schema(self):
        init_calls.append(True)

    monkeypatch.setattr(
        PostgresManager, "_initialize_schema", mock_init_schema, raising=False
    )

    mgr = PostgresManager(password="secret", min_conn=1, max_conn=4)

    fake_pool.assert_called_once()
    assert mgr.connection_pool is pool_stub
    assert init_calls == [True]


def test_log_performance_metric_executes_insert(patched_postgres):
    connection, *_ = patched_postgres
    mgr = PostgresManager(password="secret")
    mgr.log_performance_metric("stage1", 10, 2.0, worker_count=3, memory_usage_mb=128)

    query, params = connection.cursor_stub.executed[-1]
    assert "INSERT INTO performance_metrics" in query
    assert params[0] == "stage1"
    assert params[1] == 10
    assert params[2] == 2.0
    assert params[3] == pytest.approx(5.0)


def test_get_performance_metrics_returns_dicts(patched_postgres):
    connection, *_ = patched_postgres
    connection.cursor_stub.fetch_values = [{"stage": "stage1", "urls_processed": 5}]
    mgr = PostgresManager(password="secret")

    rows = mgr.get_performance_metrics(stage="stage1", limit=5)

    fetch_query, params = connection.cursor_stub.executed[-1]
    assert "stage = %s" in fetch_query
    assert params[-1] == 5
    assert rows == [{"stage": "stage1", "urls_processed": 5}]


def test_log_error_persists_record(patched_postgres):
    connection, *_ = patched_postgres
    mgr = PostgresManager(password="secret")
    mgr.log_error(
        "stage1",
        "http://example.com",
        "Timeout",
        "boom",
        http_status_code=504,
        retry_count=1,
    )

    query, params = connection.cursor_stub.executed[-1]
    assert "INSERT INTO error_logs" in query
    assert params[0] == "stage1"
    assert params[1] == "http://example.com"
    assert params[2] == "Timeout"
    assert params[6] == 1


def test_save_error_analysis_iterates_clusters(patched_postgres):
    connection, *_ = patched_postgres
    mgr = PostgresManager(password="secret")
    cluster_payload = [
        {
            "cluster_id": 1,
            "cluster_size": 10,
            "cluster_percentage": 50.0,
            "common_error_type": "Timeout",
            "common_url_pattern": "/foo",
            "avg_http_status": 504,
            "summary": "Timeout cluster",
            "recommendations": "Add retries",
        },
        {
            "cluster_id": 2,
            "cluster_size": 10,
            "cluster_percentage": 50.0,
            "summary": "Other cluster",
        },
    ]

    mgr.save_error_analysis(
        total_errors=20, num_clusters=2, cluster_data=cluster_payload
    )

    insert_calls = [
        q for q, _ in connection.cursor_stub.executed if "error_analysis_reports" in q
    ]
    assert len(insert_calls) == 2
