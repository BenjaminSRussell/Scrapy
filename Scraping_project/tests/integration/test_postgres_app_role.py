"""#538: init-db.sql creates a least-privilege app role; the app works as that role.

Runs against a throwaway local PostgreSQL cluster (initdb). Skipped when the
server binaries are not installed (e.g. CI without Postgres).
"""

import glob
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INIT_SQL = ROOT / "docker" / "init-db.sql"


def _pg_bin():
    for cand in [os.environ.get("PG_BIN", "")] + sorted(glob.glob("/usr/lib/postgresql/*/bin"), reverse=True):
        if cand and Path(cand, "initdb").exists() and Path(cand, "pg_ctl").exists():
            return Path(cand)
    found = shutil.which("initdb")
    return Path(found).parent if found and shutil.which("pg_ctl") else None


PG_BIN = _pg_bin()
psycopg2 = pytest.importorskip("psycopg2")
pytestmark = pytest.mark.skipif(PG_BIN is None or shutil.which("psql") is None or os.geteuid() == 0,
                                reason="needs PostgreSQL server binaries + psql, as a non-root user")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    base = tmp_path_factory.mktemp("pg538")
    data, sock = base / "data", base / "sock"
    sock.mkdir()
    pwfile = base / "pw"
    pwfile.write_text("supersecret\n")
    subprocess.run([PG_BIN / "initdb", "-D", data, "-U", "postgres", "--auth=scram-sha-256",
                    f"--pwfile={pwfile}"], check=True, capture_output=True)
    port = _free_port()
    subprocess.run([PG_BIN / "pg_ctl", "-D", data, "-l", base / "log", "-w", "-o",
                    f"-k {sock} -p {port} -c listen_addresses=127.0.0.1", "start"], check=True, capture_output=True)
    env = {**os.environ, "PGHOST": "127.0.0.1", "PGPORT": str(port), "PGUSER": "postgres",
           "PGPASSWORD": "supersecret"}
    try:
        subprocess.run(["psql", "-v", "ON_ERROR_STOP=1", "-d", "postgres", "-c",
                        "CREATE DATABASE scraping_pipeline"], env=env, check=True, capture_output=True)
        yield {"port": port, "env": env}
    finally:
        subprocess.run([PG_BIN / "pg_ctl", "-D", data, "-m", "immediate", "stop"], capture_output=True)


def _run_init(cluster, **app_env):
    env = {k: v for k, v in cluster["env"].items() if k not in {"APP_DB_USER", "APP_DB_PASSWORD"}}
    env.update(app_env)
    return subprocess.run(["psql", "-v", "ON_ERROR_STOP=1", "-d", "postgres", "-f", str(INIT_SQL)],
                          env=env, capture_output=True, text=True)


def _connect(cluster, user, password):
    return psycopg2.connect(host="127.0.0.1", port=cluster["port"], dbname="scraping_pipeline",
                            user=user, password=password)


def test_init_without_app_password_creates_no_role(cluster):
    r = _run_init(cluster, APP_DB_USER="ghost_app")  # no APP_DB_PASSWORD
    assert r.returncode == 0, r.stderr
    assert "no least-privilege app role created" in r.stdout + r.stderr
    with _connect(cluster, "postgres", "supersecret") as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_roles WHERE rolname = 'ghost_app'")
        assert cur.fetchone()[0] == 0


def test_app_role_is_least_privilege_and_app_works(cluster, monkeypatch):
    r = _run_init(cluster, APP_DB_PASSWORD="app-pass-1")
    assert r.returncode == 0, r.stderr
    assert _run_init(cluster, APP_DB_PASSWORD="app-pass-1").returncode == 0  # idempotent re-run

    with _connect(cluster, "postgres", "supersecret") as c, c.cursor() as cur:
        cur.execute("SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname='scrapy_app'")
        assert cur.fetchone() == (False, False, False)

    from src.utils.postgres import PostgresManager
    for k, v in {"DB_HOST": "127.0.0.1", "DB_PORT": str(cluster["port"]), "DB_NAME": "scraping_pipeline",
                 "DB_USER": "scrapy_app", "DB_PASSWORD": "app-pass-1"}.items():
        monkeypatch.setenv(k, v)
    mgr = PostgresManager()  # schema exists -> no DDL attempted as the restricted role
    try:
        with mgr.get_connection() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO performance_metrics (stage, urls_processed, processing_time_seconds) "
                        "VALUES ('stage1', 10, 1.5) RETURNING id")
            assert cur.fetchone()[0] >= 1
            cur.execute("INSERT INTO spider_stats (spider_name, urls_processed, timestamp) VALUES ('s', 1, NOW())")
            cur.execute("SELECT count(*) FROM performance_metrics")
            assert cur.fetchone()[0] >= 1
    finally:
        mgr.close()

    denied = [
        "CREATE TABLE evil (id int)",
        "DROP TABLE error_logs",
        "TRUNCATE performance_metrics",
        "DELETE FROM performance_metrics",
        "UPDATE performance_metrics SET stage = 'x'",
        "ALTER TABLE error_logs ADD COLUMN x int",
        "CREATE INDEX IF NOT EXISTS idx_perf_stage_time ON performance_metrics(stage, timestamp DESC)",
    ]
    for sql in denied:
        conn = _connect(cluster, "scrapy_app", "app-pass-1")
        try:
            with conn.cursor() as cur, pytest.raises(psycopg2.Error) as exc:
                cur.execute(sql)
            assert exc.value.pgcode in ("42501", "42P01") or "permission denied" in str(exc.value), sql
        finally:
            conn.close()


def test_rerun_rotates_password(cluster):
    assert _run_init(cluster, APP_DB_PASSWORD="app-pass-1").returncode == 0
    assert _run_init(cluster, APP_DB_PASSWORD="app-pass-2").returncode == 0
    _connect(cluster, "scrapy_app", "app-pass-2").close()
    with pytest.raises(psycopg2.OperationalError):
        _connect(cluster, "scrapy_app", "app-pass-1")


def test_restricted_role_on_empty_db_gets_clear_error(cluster, monkeypatch, caplog):
    env = cluster["env"]
    assert _run_init(cluster, APP_DB_PASSWORD="app-pass-2").returncode == 0
    subprocess.run(["psql", "-d", "postgres", "-c", "DROP DATABASE IF EXISTS emptydb"], env=env,
                   check=True, capture_output=True)
    subprocess.run(["psql", "-d", "postgres", "-c", "CREATE DATABASE emptydb"], env=env, check=True,
                   capture_output=True)
    subprocess.run(["psql", "-d", "postgres", "-c", "GRANT CONNECT ON DATABASE emptydb TO scrapy_app"],
                   env=env, check=True, capture_output=True)
    from src.utils.postgres import PostgresManager
    for k, v in {"DB_HOST": "127.0.0.1", "DB_PORT": str(cluster["port"]), "DB_NAME": "emptydb",
                 "DB_USER": "scrapy_app", "DB_PASSWORD": "app-pass-2"}.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(psycopg2.Error):
        PostgresManager()
    assert any("init-db.sql" in r.getMessage() for r in caplog.records)
