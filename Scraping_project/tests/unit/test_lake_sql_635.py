"""Read-only DuckDB SQL over Delta paths (#635).

DuckDB itself is optional. These tests drive validation, the timeout, the row
cap, and Delta file registration through a fake connection.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from src.lakehouse.sql_query import (
    LakeSqlDisabled,
    LakeSqlUnavailable,
    QueryFailed,
    QueryTimeout,
    SqlRejected,
    _files_under_table,
    query_delta,
    run_http,
    validate_select,
)
from src.utils import feature_flags as ff

DASH = Path(__file__).resolve().parents[2] / "dashboard"


def test_flag_defaults_off():
    assert ff.FLAGS["LAKE_SQL_ENABLED"] == (
        False,
        "Read-only SELECT over Delta tables via optional DuckDB (POST /api/sql) (#635).",
    )
    assert ff.get_bool("LAKE_SQL_ENABLED", False, env={}, use_config=False) is False


def test_disabled_by_default(tmp_path):
    with pytest.raises(LakeSqlDisabled):
        query_delta("SELECT 1", env={}, base_path=tmp_path)
    status, body = run_http("SELECT 1", env={}, base_path=tmp_path)
    assert status == 403 and body == {"ok": False, "error": "disabled"}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM stage2_queue LIMIT 10",
        "  select url from stage2_queue where status = 'pending'",
        "WITH c AS (SELECT 1 AS n) SELECT * FROM c",
        "SELECT 1 -- trailing comment",
        "/* note */ SELECT 'DROP TABLE stage2_queue' AS label",
        'SELECT "delete" FROM stage2_queue',
        "SELECT count(*) FROM stage2_queue;",
        "SELECT * FROM (SELECT 1 AS n) AS t",
    ],
)
def test_select_statements_pass(sql):
    cleaned = validate_select(sql)
    assert "--" not in cleaned and "/*" not in cleaned
    assert not cleaned.rstrip().endswith(";")


@pytest.mark.parametrize(
    ("sql", "match"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("DELETE FROM stage2_queue", "only SELECT"),
        ("DROP TABLE stage2_queue", "only SELECT"),
        ("INSERT INTO stage2_queue SELECT 1", "only SELECT"),
        ("WITH c AS (SELECT 1) INSERT INTO t SELECT * FROM c", "insert"),
        ("SELECT 1; DROP TABLE stage2_queue", "multiple"),
        ("SELECT 1; SELECT 2", "multiple"),
        ("SELECT * FROM stage2_queue INTO OUTFILE", "into"),
        ("SELECT * FROM read_parquet('/tmp/x.parquet')", "read_parquet"),
        ('SELECT * FROM "read_parquet"(\'/tmp/x.parquet\')', "read_parquet"),
        ("SELECT * FROM 'file.parquet'", "file paths"),
        ("SELECT * FROM t JOIN '/tmp/other.parquet' j", "file paths"),
        ("COPY stage2_queue TO '/tmp/x'", "only SELECT"),
        ("ATTACH 'lake.db'", "only SELECT"),
        ("SELECT 1) AS x UNION ALL SELECT 2", "unbalanced"),
        ("SELECT 'unterminated", "unterminated"),
        ("/* select */ drop table t", "only SELECT"),
        ("SELECT $$1$$", "dollar-quoting"),
    ],
)
def test_non_select_is_rejected(sql, match):
    with pytest.raises(SqlRejected, match=match):
        validate_select(sql)


def test_rejected_sql_does_not_open_a_connection(tmp_path):
    def open_conn():
        raise AssertionError("connection opened")

    with pytest.raises(SqlRejected):
        query_delta(
            "DELETE FROM stage2_queue",
            env={"LAKE_SQL_ENABLED": "1"},
            base_path=tmp_path,
            connect=open_conn,
        )


def test_comment_cannot_swallow_the_rest_of_the_statement():
    cleaned = validate_select("SELECT 1 -- ) AS x UNION SELECT secret FROM t")
    assert cleaned.strip() == "SELECT 1"


class _Rows:
    def __init__(self, statements, rows):
        self.statements = statements
        self.description = [("n",)]
        self._rows = rows

    def execute(self, sql):
        self.statements.append(sql)
        return self

    def fetchmany(self, count):
        return self._rows[:count]

    def close(self):
        pass

    def interrupt(self):
        pass


def test_row_cap_limits_and_reports_truncation(tmp_path):
    conn = _Rows([], [(i,) for i in range(5)])
    out = query_delta(
        "SELECT n FROM stage2_queue",
        enabled_flag=True,
        base_path=tmp_path,
        connect=lambda: conn,
        timeout_s=2,
        row_cap_n=2,
    )
    assert out["rows"] == [{"n": 0}, {"n": 1}]
    assert out["truncated"] is True
    assert out["row_count"] == 2
    assert any("LIMIT 3" in sql for sql in conn.statements)
    assert not tmp_path.exists() or not any(tmp_path.rglob("*"))


def test_relation_limit_is_preferred_over_wrapping(tmp_path):
    seen = {}

    class Rel:
        def limit(self, n, offset=0):
            seen["limit"] = n
            return self

        def fetchmany(self, count):
            seen["fetch"] = count
            return [(1,)]

        columns = ["n"]

    class Conn:
        def sql(self, statement):
            seen["sql"] = statement
            return Rel()

        def execute(self, sql):
            seen.setdefault("settings", []).append(sql)
            if not sql.lstrip().upper().startswith("SET"):
                raise AssertionError(sql)
            return self

        def close(self):
            pass

        def interrupt(self):
            pass

    out = query_delta(
        "SELECT 1 AS n",
        enabled_flag=True,
        base_path=tmp_path,
        connect=lambda: Conn(),
        timeout_s=2,
        row_cap_n=4,
    )
    assert seen["sql"] == "SELECT 1 AS n"
    assert seen["limit"] == 5
    assert out["rows"] == [{"n": 1}]
    assert out["truncated"] is False


def test_timeout_interrupts_the_statement(tmp_path):
    class Conn:
        def __init__(self):
            self.flag = threading.Event()

        def execute(self, sql):
            if sql.lstrip().upper().startswith("SET"):
                return self
            if not self.flag.wait(2):
                raise TimeoutError("hung")
            raise RuntimeError("interrupted")

        def interrupt(self):
            self.flag.set()

        def close(self):
            pass

    with pytest.raises(QueryTimeout):
        query_delta(
            "SELECT 1",
            enabled_flag=True,
            base_path=tmp_path,
            connect=lambda: Conn(),
            timeout_s=0.2,
            row_cap_n=5,
        )


def test_catalog_error_hides_the_lake_path(tmp_path):
    lake = tmp_path / "lake"
    lake.mkdir()

    class CatalogException(Exception):
        pass

    class Conn:
        def sql(self, statement):
            raise CatalogException(f"table does not exist: {lake}/stage2_queue")

        def execute(self, sql):
            return self

        def close(self):
            pass

        def interrupt(self):
            pass

    with pytest.raises(QueryFailed, match="<lake>") as raised:
        query_delta(
            "SELECT * FROM missing_table",
            enabled_flag=True,
            base_path=lake,
            connect=lambda: Conn(),
            timeout_s=2,
            row_cap_n=5,
        )
    assert str(lake) not in str(raised.value)


def test_filesystem_root_is_not_a_lake(tmp_path):
    class Conn(_Rows):
        def __init__(self):
            super().__init__([], [(1,)])

    with pytest.raises(LakeSqlUnavailable, match="not usable"):
        query_delta(
            "SELECT 1",
            enabled_flag=True,
            base_path=Path("/"),
            connect=lambda: Conn(),
            timeout_s=2,
            row_cap_n=5,
        )


def test_missing_lake_is_not_created(tmp_path):
    missing = tmp_path / "absent"

    class Conn(_Rows):
        def __init__(self):
            super().__init__([], [(1,)])

    out = query_delta(
        "SELECT 1",
        enabled_flag=True,
        base_path=missing,
        connect=lambda: Conn(),
        timeout_s=2,
        row_cap_n=5,
    )
    assert out["rows"] == [{"n": 1}]
    assert out["tables"] == []
    assert not missing.exists()


def test_parquet_outside_the_table_is_ignored(tmp_path):
    table = tmp_path / "stage2_queue"
    table.mkdir()
    outside = tmp_path / "secret.parquet"
    outside.write_bytes(b"nope")
    inside = table / "part.parquet"
    inside.write_bytes(b"")
    files = _files_under_table([outside.as_uri(), str(inside), "s3://bucket/obj"], table)
    assert files == [inside.resolve()]


@pytest.mark.delta
def test_delta_files_are_registered_as_a_relation(tmp_path):
    from src.lakehouse.lakehouse_manager import LakehouseManager

    lake = tmp_path / "lake"
    manager = LakehouseManager(base_path=str(lake), start_workers=False)
    try:
        assert manager.write(
            "stage2_queue",
            [{"url": "https://example.com/a", "url_hash": "h000", "status": "pending"}],
            async_write=False,
        ) is not False
    finally:
        manager.shutdown(timeout=1)

    seen: dict = {}

    class Rel:
        def __init__(self, files):
            self.files = files

        def create_view(self, name, replace=True):
            seen.setdefault("views", {})[name] = list(self.files)

    class Conn:
        def read_parquet(self, files, union_by_name=True):
            assert union_by_name is True
            return Rel(files)

        def execute(self, sql):
            seen.setdefault("sql", []).append(sql)
            self.description = [("url",)]
            self._rows = [("https://example.com/a",)]
            return self

        def fetchmany(self, count):
            return self._rows[:count]

        def close(self):
            pass

        def interrupt(self):
            pass

    out = query_delta(
        "SELECT url FROM stage2_queue",
        enabled_flag=True,
        base_path=lake,
        connect=lambda: Conn(),
        timeout_s=2,
        row_cap_n=10,
    )
    assert out["rows"] == [{"url": "https://example.com/a"}]
    assert "stage2_queue" in seen["views"]
    registered = seen["views"]["stage2_queue"]
    assert registered and all(path.endswith(".parquet") for path in registered)
    assert all(str(lake.resolve()) in path for path in registered)


def test_duckdb_select_when_installed(tmp_path):
    pytest.importorskip("duckdb")
    out = query_delta(
        "SELECT 1 AS n",
        enabled_flag=True,
        base_path=tmp_path / "empty",
        timeout_s=5,
        row_cap_n=10,
    )
    assert out["ok"] is True
    assert out["row_count"] == 1
    assert out["rows"][0]["n"] == 1
    assert not (tmp_path / "empty").exists()


@pytest.mark.delta
def test_duckdb_queries_delta_rows_when_installed(tmp_path):
    pytest.importorskip("duckdb")
    pa = pytest.importorskip("pyarrow")
    write_deltalake = pytest.importorskip("deltalake").write_deltalake
    lake = tmp_path / "lake"
    write_deltalake(
        str(lake / "stage2_queue"),
        pa.table({"url": ["https://example.com/a"], "status": ["pending"]}),
    )
    out = query_delta(
        "SELECT status FROM stage2_queue WHERE url LIKE '%example.com%'",
        enabled_flag=True,
        base_path=lake,
        timeout_s=5,
        row_cap_n=10,
    )
    assert out["rows"] == [{"status": "pending"}]
    assert out["tables"] == ["stage2_queue"]


def test_duckdb_timeout_when_installed(tmp_path):
    pytest.importorskip("duckdb")
    with pytest.raises(QueryTimeout):
        query_delta(
            "SELECT count(*) FROM range(100000000000)",
            enabled_flag=True,
            base_path=tmp_path / "absent",
            timeout_s=0.5,
            row_cap_n=5,
        )


# ---- Control Center /api/sql ----

@pytest.fixture(scope="module")
def serve():
    spec = importlib.util.spec_from_file_location("_cc_serve_sql", DASH / "serve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _handler(serve, root, env):
    return type(
        "H",
        (serve.DashboardHandler,),
        {
            "root": root,
            "log_message": lambda *a: None,
            "auth": serve.AuthGate.from_env(env),
            "limiter": serve.RateLimiter.from_env(env),
            "cors_origins": serve.parse_cors_origins(env),
            "csp": serve.build_csp(env),
            "metrics_upstream": env.get("METRICS_UPSTREAM", serve.DEFAULT_METRICS_UPSTREAM),
            "upstream_timeout": float(env.get("CC_UPSTREAM_TIMEOUT", 4)),
            "queue_keys": serve.queue_keys(env),
        },
    )


@pytest.fixture
def server(serve, tmp_path):
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html><title>CC</title>")
    servers = []

    def start(env):
        handler = _handler(serve, root, env)
        httpd = serve.make_server("127.0.0.1", 0, handler=handler, env=env)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def _request(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_http_sql_is_disabled_by_default(server, monkeypatch):
    monkeypatch.delenv("LAKE_SQL_ENABLED", raising=False)
    base = server({})
    status, body = _request(base + "/api/sql")
    assert status == 200
    assert json.loads(body)["enabled"] is False
    status, body = _request(
        base + "/api/sql",
        data=b'{"sql":"SELECT 1"}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    assert status == 403
    assert json.loads(body)["error"] == "disabled"


def test_http_rejects_non_select_when_enabled(server, monkeypatch):
    monkeypatch.setenv("LAKE_SQL_ENABLED", "1")
    base = server({})
    status, body = _request(
        base + "/api/sql",
        data=b'{"sql":"DELETE FROM stage2_queue"}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    assert status == 400
    assert "SELECT" in json.loads(body)["error"]


def test_http_sql_requires_auth_when_auth_is_on(server):
    base = server({"CC_AUTH_TOKEN": "tok-sql"})
    status, _body = _request(
        base + "/api/sql",
        data=b'{"sql":"SELECT 1"}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    assert status == 401


def test_http_sql_body_is_capped(server, monkeypatch):
    monkeypatch.delenv("LAKE_SQL_ENABLED", raising=False)
    base = server({})
    payload = b'{"sql":"' + (b"A" * 20000) + b'"}'
    status, body = _request(
        base + "/api/sql",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    assert status == 413
    assert json.loads(body)["error"] == "body too large"


def test_http_post_other_routes_are_not_sql(server):
    base = server({})
    status, _body = _request(base + "/api/health", data=b"{}", method="POST")
    assert status == 405
